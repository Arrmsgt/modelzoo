from __future__ import absolute_import
from __future__ import unicode_literals
from __future__ import print_function
from __future__ import division

import argparse
import os
import shutil
import time
import math
import warnings
import models
from utils import convert_model, measure_model

# [修改] 导入torch_sdaa模块
import torch
import torch_sdaa
import torch.nn as nn
import torch.nn.parallel
import torch.distributed as dist
import torch.optim
import torch.utils.data
import torch.utils.data.distributed
import torchvision.transforms as transforms
import torchvision.datasets as datasets

parser = argparse.ArgumentParser(description='PyTorch Condensed Convolutional Networks')
parser.add_argument('data', metavar='DIR',
                    help='path to dataset')
parser.add_argument('--model', default='condensenet', type=str, metavar='M',
                    help='model to train the dataset')
parser.add_argument('-j', '--workers', default=8, type=int, metavar='N',
                    help='number of data loading workers (default: 4)')
parser.add_argument('--epochs', default=120, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
parser.add_argument('-b', '--batch-size', default=256, type=int,
                    metavar='N', help='mini-batch size (default: 256)')
parser.add_argument('--lr', '--learning-rate', default=0.1, type=float,
                    metavar='LR', help='initial learning rate (default: 0.1)')
parser.add_argument('--lr-type', default='cosine', type=str, metavar='T',
                    help='learning rate strategy (default: cosine)',
                    choices=['cosine', 'multistep'])
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum (default: 0.9)')
parser.add_argument('--weight-decay', '--wd', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)')
parser.add_argument('--print-freq', '-p', default=10, type=int,
                    metavar='N', help='print frequency (default: 10)')
parser.add_argument('--pretrained', dest='pretrained', action='store_true',
                    help='use pre-trained model (default: false)')
parser.add_argument('--no-save-model', dest='no_save_model', action='store_true',
                    help='only save best model (default: false)')
parser.add_argument('--manual-seed', default=0, type=int, metavar='N',
                    help='manual seed (default: 0)')
parser.add_argument('--gpu',
                    help='gpu available')

# [修改] 添加分布式训练相关参数
parser.add_argument('--world-size', default=1, type=int,
                    help='number of distributed processes')
parser.add_argument('--dist-url', default='env://', type=str,
                    help='url used to set up distributed training')
parser.add_argument('--dist-backend', default='tccl', type=str,
                    help='distributed backend')
parser.add_argument('--rank', default=0, type=int,
                    help='node rank for distributed training')
parser.add_argument('--local_rank', default=0, type=int,
                    help='local rank for distributed training')

parser.add_argument('--savedir', type=str, metavar='PATH', default='results/savedir',
                    help='path to save result and checkpoint (default: results/savedir)')
parser.add_argument('--resume', action='store_true',
                    help='use latest checkpoint if have any (default: none)')

parser.add_argument('--stages', type=str, metavar='STAGE DEPTH',
                    help='per layer depth')
parser.add_argument('--bottleneck', default=4, type=int, metavar='B',
                    help='bottleneck (default: 4)')
parser.add_argument('--group-1x1', type=int, metavar='G', default=4,
                    help='1x1 group convolution (default: 4)')
parser.add_argument('--group-3x3', type=int, metavar='G', default=4,
                    help='3x3 group convolution (default: 4)')
parser.add_argument('--condense-factor', type=int, metavar='C', default=4,
                    help='condense factor (default: 4)')
parser.add_argument('--growth', type=str, metavar='GROWTH RATE',
                    help='per layer growth')
parser.add_argument('--reduction', default=0.5, type=float, metavar='R',
                    help='transition reduction (default: 0.5)')
parser.add_argument('--dropout-rate', default=0, type=float,
                    help='drop out (default: 0)')
parser.add_argument('--group-lasso-lambda', default=0., type=float, metavar='LASSO',
                    help='group lasso loss weight (default: 0)')

parser.add_argument('--evaluate', action='store_true',
                    help='evaluate model on validation set (default: false)')
parser.add_argument('--convert-from', default=None, type=str, metavar='PATH',
                    help='path to saved checkpoint (default: none)')
parser.add_argument('--evaluate-from', default=None, type=str, metavar='PATH',
                    help='path to saved checkpoint (default: none)')

args = parser.parse_args()

# [修改] 设置分布式训练环境
if 'RANK' in os.environ and 'WORLD_SIZE' in os.environ:
    args.rank = int(os.environ["RANK"])
    args.world_size = int(os.environ['WORLD_SIZE'])
    args.gpu = int(os.environ['LOCAL_RANK'])

# [修改] 注释掉原始的GPU设置，将在后面使用device设置
# os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu

args.stages = list(map(int, args.stages.split('-')))
args.growth = list(map(int, args.growth.split('-')))
if args.condense_factor is None:
    args.condense_factor = args.group_1x1

if args.data == 'cifar10':
    args.num_classes = 10
elif args.data == 'cifar100':
    args.num_classes = 100
else:
    args.num_classes = 1000

warnings.filterwarnings("ignore")

# [修改] 将torch.cuda改为torch.sdaa
torch.manual_seed(args.manual_seed)
torch.sdaa.manual_seed_all(args.manual_seed)

best_prec1 = 0


def main():
    global args, best_prec1

    # [修改] 初始化分布式训练
    if args.world_size > 1:
        device = torch.device(f"sdaa:{args.gpu}")
        torch.sdaa.set_device(device)
        dist.init_process_group(backend='tccl', init_method=args.dist_url,
                                world_size=args.world_size, rank=args.rank)
    else:
        device = torch.device('sdaa' if torch.sdaa.is_available() else 'cpu')

    ### Calculate FLOPs & Param
    model = getattr(models, args.model)(args)
    print(model)
    if args.data in ['cifar10', 'cifar100']:
        IMAGE_SIZE = 32
    else:
        IMAGE_SIZE = 224
    n_flops, n_params = measure_model(model, IMAGE_SIZE, IMAGE_SIZE)
    print('FLOPs: %.2fM, Params: %.2fM' % (n_flops / 1e6, n_params / 1e6))
    args.filename = "%s_%s_%s.txt" % \
                    (args.model, int(n_params), int(n_flops))
    del (model)
    print(args)

    ### Create model
    model = getattr(models, args.model)(args)

    # [修改] 使用sdaa设备而不是cuda
    if args.world_size > 1:
        model = model.to(device)
        model = torch.nn.parallel.DistributedDataParallel(model, device_ids=[args.gpu])
    else:
        if args.model.startswith('alexnet') or args.model.startswith('vgg'):
            model.features = torch.nn.DataParallel(model.features)
            model = model.to(device)
        else:
            model = torch.nn.DataParallel(model).to(device)

    # [修改] 初始化AMP混合精度训练
    scaler = torch.sdaa.amp.GradScaler()

    ### Define loss function (criterion) and optimizer
    # [修改] 使用sdaa设备
    criterion = nn.CrossEntropyLoss().to(device)
    optimizer = torch.optim.SGD(model.parameters(), args.lr,
                                momentum=args.momentum,
                                weight_decay=args.weight_decay,
                                nesterov=True)

    ### Optionally resume from a checkpoint
    if args.resume:
        checkpoint = load_checkpoint(args)
        if checkpoint is not None:
            args.start_epoch = checkpoint['epoch'] + 1
            best_prec1 = checkpoint['best_prec1']
            model.load_state_dict(checkpoint['state_dict'])
            optimizer.load_state_dict(checkpoint['optimizer'])
            # [修改] 加载scaler状态
            if 'scaler' in checkpoint:
                scaler.load_state_dict(checkpoint['scaler'])

    ### Optionally convert from a model
    if args.convert_from is not None:
        args.evaluate = True
        state_dict = torch.load(args.convert_from)['state_dict']
        model.load_state_dict(state_dict)
        model = model.cpu().module
        convert_model(model, args)
        # [修改] 使用sdaa设备
        model = model.to(device)
        if args.world_size > 1:
            model = torch.nn.parallel.DistributedDataParallel(model, device_ids=[args.gpu])
        else:
            model = nn.DataParallel(model).to(device)
        head, tail = os.path.split(args.convert_from)
        tail = "converted_" + tail
        torch.save({'state_dict': model.state_dict()}, os.path.join(head, tail))

    ### Optionally evaluate from a model
    if args.evaluate_from is not None:
        args.evaluate = True
        state_dict = torch.load(args.evaluate_from)['state_dict']
        model.load_state_dict(state_dict)

    # [修改] 移除cudnn.benchmark，因为不使用CUDA
    # cudnn.benchmark = True

    ### Data loading
    # [修改] 更新数据集路径到/data/datasets/
    if args.data == "cifar10":
        normalize = transforms.Normalize(mean=[0.4914, 0.4824, 0.4467],
                                         std=[0.2471, 0.2435, 0.2616])
        train_set = datasets.CIFAR10('/data/datasets/', train=True, download=True,
                                     transform=transforms.Compose([
                                         transforms.RandomCrop(32, padding=4),
                                         transforms.RandomHorizontalFlip(),
                                         transforms.ToTensor(),
                                         normalize,
                                     ]))
        val_set = datasets.CIFAR10('/data/datasets/', train=False,
                                   transform=transforms.Compose([
                                       transforms.ToTensor(),
                                       normalize,
                                   ]))
    elif args.data == "cifar100":
        normalize = transforms.Normalize(mean=[0.5071, 0.4867, 0.4408],
                                         std=[0.2675, 0.2565, 0.2761])
        train_set = datasets.CIFAR100('/data/datasets/', train=True, download=True,
                                      transform=transforms.Compose([
                                          transforms.RandomCrop(32, padding=4),
                                          transforms.RandomHorizontalFlip(),
                                          transforms.ToTensor(),
                                          normalize,
                                      ]))
        val_set = datasets.CIFAR100('/data/datasets/', train=False,
                                    transform=transforms.Compose([
                                        transforms.ToTensor(),
                                        normalize,
                                    ]))
    else:
        traindir = os.path.join(args.data, 'train')
        valdir = os.path.join(args.data, 'val')
        normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                         std=[0.229, 0.224, 0.225])
        train_set = datasets.ImageFolder(traindir, transforms.Compose([
            transforms.RandomResizedCrop(224),  # [修改] RandomSizedCrop已弃用，使用RandomResizedCrop
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            normalize,
        ]))

        val_set = datasets.ImageFolder(valdir, transforms.Compose([
            transforms.Resize(256),  # [修改] Scale已弃用，使用Resize
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            normalize,
        ]))

    # [修改] 添加分布式采样器
    if args.world_size > 1:
        train_sampler = torch.utils.data.distributed.DistributedSampler(train_set)
    else:
        train_sampler = None

    train_loader = torch.utils.data.DataLoader(
        train_set,
        batch_size=args.batch_size, shuffle=(train_sampler is None),
        num_workers=args.workers, pin_memory=True, sampler=train_sampler)

    val_loader = torch.utils.data.DataLoader(
        val_set,
        batch_size=args.batch_size, shuffle=False,
        num_workers=args.workers, pin_memory=True)

    if args.evaluate:
        validate(val_loader, model, criterion, device)
        return

    for epoch in range(args.start_epoch, args.epochs):
        # [修改] 在每个epoch开始时设置采样器epoch
        if args.world_size > 1:
            train_sampler.set_epoch(epoch)

        ### Train for one epoch
        tr_prec1, tr_prec5, loss, lr = \
            train(train_loader, model, criterion, optimizer, epoch, device, scaler)

        ### Evaluate on validation set
        val_prec1, val_prec5 = validate(val_loader, model, criterion, device)

        # [修改] 只在主进程保存模型
        if args.rank == 0 or args.world_size == 1:
            ### Remember best prec@1 and save checkpoint
            is_best = val_prec1 < best_prec1
            best_prec1 = max(val_prec1, best_prec1)
            model_filename = 'checkpoint_%03d.pth.tar' % epoch
            save_checkpoint({
                'epoch': epoch,
                'model': args.model,
                'state_dict': model.state_dict(),
                'best_prec1': best_prec1,
                'optimizer': optimizer.state_dict(),
                'scaler': scaler.state_dict(),  # [修改] 保存scaler状态
            }, args, is_best, model_filename, "%.4f %.4f %.4f %.4f %.4f %.4f\n" %
                                              (val_prec1, val_prec5, tr_prec1, tr_prec5, loss, lr))

    # [修改] 只在主进程执行模型转换
    if args.rank == 0 or args.world_size == 1:
        ### Convert model and test
        model = model.cpu().module
        convert_model(model, args)
        model = model.to(device)
        if args.world_size > 1:
            model = torch.nn.parallel.DistributedDataParallel(model, device_ids=[args.gpu])
        else:
            model = nn.DataParallel(model).to(device)
        print(model)
        validate(val_loader, model, criterion, device)
        n_flops, n_params = measure_model(model, IMAGE_SIZE, IMAGE_SIZE)
        print('FLOPs: %.2fM, Params: %.2fM' % (n_flops / 1e6, n_params / 1e6))
    return


def train(train_loader, model, criterion, optimizer, epoch, device, scaler):
    batch_time = AverageMeter()
    data_time = AverageMeter()
    losses = AverageMeter()
    top1 = AverageMeter()
    top5 = AverageMeter()
    learned_module_list = []

    ### Switch to train mode
    model.train()
    ### Find all learned convs to prepare for group lasso loss
    for m in model.modules():
        if m.__str__().startswith('LearnedGroupConv'):
            learned_module_list.append(m)
    running_lr = None

    end = time.time()
    for i, (input, target) in enumerate(train_loader):
        progress = float(epoch * len(train_loader) + i) / \
                   (args.epochs * len(train_loader))
        args.progress = progress
        ### Adjust learning rate
        lr = adjust_learning_rate(optimizer, epoch, args, batch=i,
                                  nBatch=len(train_loader), method=args.lr_type)
        if running_lr is None:
            running_lr = lr

        ### Measure data loading time
        data_time.update(time.time() - end)

        # [修改] 数据移至SDAA设备并转换为channels_last格式
        input = input.to(device, non_blocking=True).to(memory_format=torch.channels_last)
        target = target.to(device, non_blocking=True)

        # [修改] 使用AMP混合精度训练
        with torch.sdaa.amp.autocast():
            ### Compute output
            output = model(input, progress)
            loss = criterion(output, target)

            ### Add group lasso loss
            if args.group_lasso_lambda > 0:
                lasso_loss = 0
                for m in learned_module_list:
                    lasso_loss = lasso_loss + m.lasso_loss
                loss = loss + args.group_lasso_lambda * lasso_loss

        ### Measure accuracy and record loss
        prec1, prec5 = accuracy(output.data, target, topk=(1, 5))
        losses.update(loss.item(), input.size(0))
        top1.update(prec1.item(), input.size(0))
        top5.update(prec5.item(), input.size(0))

        ### Compute gradient and do SGD step
        # [修改] 使用AMP进行梯度缩放
        optimizer.zero_grad()
        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()

        ### Measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()

        if i % args.print_freq == 0:
            print('Epoch: [{0}][{1}/{2}]\t'
                  'Time {batch_time.val:.3f}\t'  # ({batch_time.avg:.3f}) '
                  'Data {data_time.val:.3f}\t'  # ({data_time.avg:.3f}) '
                  'Loss {loss.val:.4f}\t'  # ({loss.avg:.4f}) '
                  'Prec@1 {top1.val:.3f}\t'  # ({top1.avg:.3f}) '
                  'Prec@5 {top5.val:.3f}\t'  # ({top5.avg:.3f})'
                  'lr {lr: .4f}'.format(
                epoch, i, len(train_loader), batch_time=batch_time,
                data_time=data_time, loss=losses, top1=top1, top5=top5, lr=lr))
    return 100. - top1.avg, 100. - top5.avg, losses.avg, running_lr


def validate(val_loader, model, criterion, device):
    batch_time = AverageMeter()
    losses = AverageMeter()
    top1 = AverageMeter()
    top5 = AverageMeter()

    ### Switch to evaluate mode
    model.eval()

    # [修改] 使用torch.no_grad()代替已弃用的volatile=True
    with torch.no_grad():
        end = time.time()
        for i, (input, target) in enumerate(val_loader):
            # [修改] 数据移至SDAA设备并转换为channels_last格式
            input = input.to(device, non_blocking=True).to(memory_format=torch.channels_last)
            target = target.to(device, non_blocking=True)

            # [修改] 使用AMP混合精度
            with torch.sdaa.amp.autocast():
                ### Compute output
                output = model(input)
                loss = criterion(output, target)

            ### Measure accuracy and record loss
            prec1, prec5 = accuracy(output.data, target, topk=(1, 5))
            losses.update(loss.item(), input.size(0))
            top1.update(prec1.item(), input.size(0))
            top5.update(prec5.item(), input.size(0))

            ### Measure elapsed time
            batch_time.update(time.time() - end)
            end = time.time()

            if i % args.print_freq == 0:
                print('Test: [{0}/{1}]\t'
                      'Time {batch_time.val:.3f} ({batch_time.avg:.3f})\t'
                      'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                      'Prec@1 {top1.val:.3f} ({top1.avg:.3f})\t'
                      'Prec@5 {top5.val:.3f} ({top5.avg:.3f})'.format(
                    i, len(val_loader), batch_time=batch_time, loss=losses,
                    top1=top1, top5=top5))

        print(' * Prec@1 {top1.avg:.3f} Prec@5 {top5.avg:.3f}'
              .format(top1=top1, top5=top5))

    return 100. - top1.avg, 100. - top5.avg


def load_checkpoint(args):
    model_dir = os.path.join(args.savedir, 'save_models')
    latest_filename = os.path.join(model_dir, 'latest.txt')
    if os.path.exists(latest_filename):
        with open(latest_filename, 'r') as fin:
            model_filename = fin.readlines()[0]
    else:
        return None
    print("=> loading checkpoint '{}'".format(model_filename))
    # [修改] 添加map_location参数确保在正确设备上加载
    state = torch.load(model_filename, map_location=f'sdaa:{args.gpu}')
    print("=> loaded checkpoint '{}'".format(model_filename))
    return state


def save_checkpoint(state, args, is_best, filename, result):
    print(args)
    result_filename = os.path.join(args.savedir, args.filename)
    model_dir = os.path.join(args.savedir, 'save_models')
    model_filename = os.path.join(model_dir, filename)
    latest_filename = os.path.join(model_dir, 'latest.txt')
    best_filename = os.path.join(model_dir, 'model_best.pth.tar')
    os.makedirs(args.savedir, exist_ok=True)
    os.makedirs(model_dir, exist_ok=True)
    print("=> saving checkpoint '{}'".format(model_filename))
    with open(result_filename, 'a') as fout:
        fout.write(result)
    torch.save(state, model_filename)
    with open(latest_filename, 'w') as fout:
        fout.write(model_filename)
    if args.no_save_model:
        shutil.move(model_filename, best_filename)
    elif is_best:
        shutil.copyfile(model_filename, best_filename)

    print("=> saved checkpoint '{}'".format(model_filename))
    return


class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


def adjust_learning_rate(optimizer, epoch, args, batch=None,
                         nBatch=None, method='cosine'):
    if method == 'cosine':
        T_total = args.epochs * nBatch
        T_cur = (epoch % args.epochs) * nBatch + batch
        lr = 0.5 * args.lr * (1 + math.cos(math.pi * T_cur / T_total))
    elif method == 'multistep':
        if args.data in ['cifar10', 'cifar100']:
            lr, decay_rate = args.lr, 0.1
            if epoch >= args.epochs * 0.75:
                lr *= decay_rate ** 2
            elif epoch >= args.epochs * 0.5:
                lr *= decay_rate
        else:
            """Sets the learning rate to the initial LR decayed by 10 every 30 epochs"""
            lr = args.lr * (0.1 ** (epoch // 30))
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr
    return lr


def accuracy(output, target, topk=(1,)):
    """Computes the precision@k for the specified values of k"""
    maxk = max(topk)
    batch_size = target.size(0)

    _, pred = output.topk(maxk, 1, True, True)
    pred = pred.t()
    correct = pred.eq(target.view(1, -1).expand_as(pred))

    res = []
    for k in topk:
        # [修改] 使用reshape代替view，更安全
        correct_k = correct[:k].reshape(-1).float().sum(0)
        res.append(correct_k.mul_(100.0 / batch_size))
    return res


if __name__ == '__main__':
    main()