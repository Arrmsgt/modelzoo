# Adapted to tecorigin hardware
import copy
import datetime
import os
import time

#导入torch_sdaa模块
import torch_sdaa

import torch
import torch.ao.quantization
import torch.utils.data
import torchvision
import utils
from torch import nn
from train import evaluate, load_data, train_one_epoch


def main(args):
    if args.output_dir:
        utils.mkdir(args.output_dir)

    utils.init_distributed_mode(args)
    print(args)

    if args.post_training_quantize and args.distributed:
        raise RuntimeError("Post training quantization example should not be performed on distributed mode")

    # Set backend engine to ensure that quantized model runs on the correct kernels
    if args.qbackend not in torch.backends.quantized.supported_engines:
        raise RuntimeError("Quantized backend not supported: " + str(args.qbackend))
    torch.backends.quantized.engine = args.qbackend

    device = torch.device(args.device)
    torch.backends.cudnn.benchmark = True

    # Data loading code
    print("Loading data")
    train_dir = os.path.join(args.data_path, "train")
    val_dir = os.path.join(args.data_path, "val")

    dataset, dataset_test, train_sampler, test_sampler = load_data(train_dir, val_dir, args)
    data_loader = torch.utils.data.DataLoader(
        dataset, batch_size=args.batch_size, sampler=train_sampler, num_workers=args.workers, pin_memory=True
    )

    data_loader_test = torch.utils.data.DataLoader(
        dataset_test, batch_size=args.eval_batch_size, sampler=test_sampler, num_workers=args.workers, pin_memory=True
    )

    print("Creating model", args.model)
    # when training quantized models, we always start from a pre-trained fp32 reference model
    prefix = "quantized_"
    model_name = args.model
    if not model_name.startswith(prefix):
        model_name = prefix + model_name
    model = torchvision.models.get_model(model_name, weights=args.weights, quantize=args.test_only)
    model.to(device)

    if not (args.test_only or args.post_training_quantize):
        model.fuse_model(is_qat=True)
        model.qconfig = torch.ao.quantization.get_default_qat_qconfig(args.qbackend)
        torch.ao.quantization.prepare_qat(model, inplace=True)

        if args.distributed and args.sync_bn:
            model = torch.nn.SyncBatchNorm.convert_sync_batchnorm(model)

        optimizer = torch.optim.SGD(
            model.parameters(), lr=args.lr, momentum=args.momentum, weight_decay=args.weight_decay
        )

        lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=args.lr_step_size, gamma=args.lr_gamma)

    criterion = nn.CrossEntropyLoss()
    model_without_ddp = model
    if args.distributed:
        model = torch.nn.parallel.DistributedDataParallel(model, device_ids=[args.gpu])
        model_without_ddp = model.module

    if args.resume:
        checkpoint = torch.load(args.resume, map_location="cpu", weights_only=True)
        model_without_ddp.load_state_dict(checkpoint["model"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        lr_scheduler.load_state_dict(checkpoint["lr_scheduler"])
        args.start_epoch = checkpoint["epoch"] + 1

    if args.post_training_quantize:
        # perform calibration on a subset of the training dataset
        # for that, create a subset of the training dataset
        ds = torch.utils.data.Subset(dataset, indices=list(range(args.batch_size * args.num_calibration_batches)))
        data_loader_calibration = torch.utils.data.DataLoader(
            ds, batch_size=args.batch_size, shuffle=False, num_workers=args.workers, pin_memory=True
        )
        model.eval()
        model.fuse_model(is_qat=False)
        model.qconfig = torch.ao.quantization.get_default_qconfig(args.qbackend)
        torch.ao.quantization.prepare(model, inplace=True)
        # Calibrate first
        print("Calibrating")
        evaluate(model, criterion, data_loader_calibration, device=device, print_freq=1)
        torch.ao.quantization.convert(model, inplace=True)
        if args.output_dir:
            print("Saving quantized model")
            if utils.is_main_process():
                torch.save(model.state_dict(), os.path.join(args.output_dir, "quantized_post_train_model.pth"))
        print("Evaluating post-training quantized model")
        evaluate(model, criterion, data_loader_test, device=device)
        return

    if args.test_only:
        evaluate(model, criterion, data_loader_test, device=device)
        return

    model.apply(torch.ao.quantization.enable_observer)
    model.apply(torch.ao.quantization.enable_fake_quant)
    start_time = time.time()
    for epoch in range(args.start_epoch, args.epochs):
        if args.distributed:
            train_sampler.set_epoch(epoch)
        print("Starting training for epoch", epoch)
        train_one_epoch(model, criterion, optimizer, data_loader, device, epoch, args)
        lr_scheduler.step()
        with torch.inference_mode():
            if epoch >= args.num_observer_update_epochs:
                print("Disabling observer for subseq epochs, epoch = ", epoch)
                model.apply(torch.ao.quantization.disable_observer)
            if epoch >= args.num_batch_norm_update_epochs:
                print("Freezing BN for subseq epochs, epoch = ", epoch)
                model.apply(torch.nn.intrinsic.qat.freeze_bn_stats)
            print("Evaluate QAT model")

            evaluate(model, criterion, data_loader_test, device=device, log_suffix="QAT")
            quantized_eval_model = copy.deepcopy(model_without_ddp)
            quantized_eval_model.eval()
            quantized_eval_model.to(torch.device("cpu"))
            torch.ao.quantization.convert(quantized_eval_model, inplace=True)

            print("Evaluate Quantized model")
            evaluate(quantized_eval_model, criterion, data_loader_test, device=torch.device("cpu"))

        model.train()

        if args.output_dir:
            checkpoint = {
                "model": model_without_ddp.state_dict(),
                "eval_model": quantized_eval_model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "lr_scheduler": lr_scheduler.state_dict(),
                "epoch": epoch,
                "args": args,
            }
            utils.save_on_master(checkpoint, os.path.join(args.output_dir, f"model_{epoch}.pth"))
            utils.save_on_master(checkpoint, os.path.join(args.output_dir, "checkpoint.pth"))
        print("Saving models after epoch ", epoch)

    total_time = time.time() - start_time
    total_time_str = str(datetime.timedelta(seconds=int(total_time)))
    print(f"Training time {total_time_str}")


def get_args_parser(add_help=True):
    import argparse

    parser = argparse.ArgumentParser(description="PyTorch Quantized Classification Training", add_help=add_help)

    parser.add_argument("--data-path", default="/datasets01/imagenet_full_size/061417/", type=str, help="dataset path")
    parser.add_argument("--model", default="mobilenet_v2", type=str, help="model name")
    parser.add_argument("--qbackend", default="qnnpack", type=str, help="Quantized backend: fbgemm or qnnpack")
    #将使用设备更改为sdaa
    #parser.add_argument("--device", default="cuda", type=str, help="device (Use cuda or cpu Default: cuda)")
    parser.add_argument("--device", default="sdaa", type=str, help="device (Use sdaa or cpu Default: sdaa)")

    parser.add_argument(
        "-b", "--batch-size", default=32, type=int, help="images per gpu, the total batch size is $NGPU x batch_size"
    )
    parser.add_argument("--eval-batch-size", default=128, type=int, help="batch size for evaluation")
    parser.add_argument("--epochs", default=90, type=int, metavar="N", help="number of total epochs to run")
    parser.add_argument(
        "--num-observer-update-epochs",
        default=4,
        type=int,
        metavar="N",
        help="number of total epochs to update observers",
    )
    parser.add_argument(
        "--num-batch-norm-update-epochs",
        default=3,
        type=int,
        metavar="N",
        help="number of total epochs to update batch norm stats",
    )
    parser.add_argument(
        "--num-calibration-batches",
        default=32,
        type=int,
        metavar="N",
        help="number of batches of training set for \
                              observer calibration ",
    )

    parser.add_argument(
        "-j", "--workers", default=16, type=int, metavar="N", help="number of data loading workers (default: 16)"
    )
    parser.add_argument("--lr", default=0.0001, type=float, help="initial learning rate")
    parser.add_argument("--momentum", default=0.9, type=float, metavar="M", help="momentum")
    parser.add_argument(
        "--wd",
        "--weight-decay",
        default=1e-4,
        type=float,
        metavar="W",
        help="weight decay (default: 1e-4)",
        dest="weight_decay",
    )
    parser.add_argument("--lr-step-size", default=30, type=int, help="decrease lr every step-size epochs")
    parser.add_argument("--lr-gamma", default=0.1, type=float, help="decrease lr by a factor of lr-gamma")
    parser.add_argument("--print-freq", default=10, type=int, help="print frequency")
    parser.add_argument("--output-dir", default=".", type=str, help="path to save outputs")
    parser.add_argument("--resume", default="", type=str, help="path of checkpoint")
    parser.add_argument("--start-epoch", default=0, type=int, metavar="N", help="start epoch")
    parser.add_argument(
        "--cache-dataset",
        dest="cache_dataset",
        help="Cache the datasets for quicker initialization. \
             It also serializes the transforms",
        action="store_true",
    )
    parser.add_argument(
        "--sync-bn",
        dest="sync_bn",
        help="Use sync batch norm",
        action="store_true",
    )
    parser.add_argument(
        "--test-only",
        dest="test_only",
        help="Only test the model",
        action="store_true",
    )
    parser.add_argument(
        "--post-training-quantize",
        dest="post_training_quantize",
        help="Post training quantize the model",
        action="store_true",
    )

    # distributed training parameters
    parser.add_argument("--world-size", default=1, type=int, help="number of distributed processes")
    parser.add_argument("--dist-url", default="env://", type=str, help="url used to set up distributed training")

    parser.add_argument(
        "--interpolation", default="bilinear", type=str, help="the interpolation method (default: bilinear)"
    )
    parser.add_argument(
        "--val-resize-size", default=256, type=int, help="the resize size used for validation (default: 256)"
    )
    parser.add_argument(
        "--val-crop-size", default=224, type=int, help="the central crop size used for validation (default: 224)"
    )
    parser.add_argument(
        "--train-crop-size", default=224, type=int, help="the random crop size used for training (default: 224)"
    )
    parser.add_argument("--clip-grad-norm", default=None, type=float, help="the maximum gradient norm (default None)")
    parser.add_argument("--weights", default=None, type=str, help="the weights enum name to load")

    parser.add_argument("--backend", default="PIL", type=str.lower, help="PIL or tensor - case insensitive")
    parser.add_argument("--use-v2", action="store_true", help="Use V2 transforms")

    return parser


if __name__ == "__main__":
    args = get_args_parser().parse_args()
    if args.backend in ("fbgemm", "qnnpack"):
        raise ValueError(
            "The --backend parameter has been re-purposed to specify the backend of the transforms (PIL or Tensor) "
            "instead of the quantized backend. Please use the --qbackend parameter to specify the quantized backend."
        )
    main(args)
