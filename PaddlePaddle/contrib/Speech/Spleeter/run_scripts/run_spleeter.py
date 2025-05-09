#encoding=utf-8
import os
from argument import config

cmd = config()
mode = cmd.mode
device = ",".join([str(i) for i in range(cmd.nproc_per_node)])
assert cmd.model_name == '2instruments',"Only 2 instruments are supported."

# 设置环境变量
os.environ["CUSTOM_DEVICE_BLACK_LIST"] = "conv2d,conv2d_grad"
os.environ["SDAA_VISIBLE_DEVICES"] = device
os.environ["PADDLE_XCCL_BACKEND"] = "sdaa"

params = {
    ### Dataset ###
    'margin': cmd.margin,
    'chunk_duration': cmd.chunk_duration,
    'sample_rate': cmd.sample_rate,
    'frame_length': cmd.frame_length,
    'frame_step': cmd.frame_step,
    'T': cmd.T,
    'F': cmd.F,
    'n_chunks_per_song': cmd.n_chunks_per_song,
    'train_manifest': cmd.train_manifest, # Manifest generated by preprocess.py
    'train_dataset' : r'dataset',

    ### Train ###
    'epochs': cmd.epoch if mode != "process_data" else 9999999,
    'batch_size': cmd.batch_size if mode != "process_data" else 1,
    'optimizer': cmd.optimizer,
    'loss': cmd.loss,
    'momentum': cmd.momentum,
    'dampening': cmd.dampening,
    'lr': cmd.lr,
    'lr_decay': cmd.lr_decay,
    'wd': cmd.wd,
    'model_dir': cmd.model_dir,
    'load_optimizer': cmd.load_optimizer,
    'start': cmd.start,
    'load_model': cmd.load_model, 
    'num_instruments': ['vocal', 'instrumental'],
    'seed': cmd.seed,
    'keep_ckpt' : cmd.keep_ckpt,
    'trainer' : cmd.trainer,
    'clean_logs' : cmd.clean_logs,
    'amp' : cmd.amp
}
'''
import json

with open('config.json','w',encoding='utf-8') as c:
    json.dump(params, c, ensure_ascii = False, indent = 4)
'''


if __name__ == "__main__":
    # 对于处理数据或者是单卡训练的，用这个进行启动
    if mode == "process_data" or cmd.nproc_per_node == 1:
        exit(os.system(f"python ori_train.py {mode} \"{params}\""))
    elif mode in ["fast_train","train"] and cmd.nproc_per_node >= 1:
        exit(os.system(f"python -m paddle.distributed.launch --devices={device} ori_train.py {mode} \"{params}\""))
    elif cmd.nproc_per_node > 1 and mode == "process_data":
        raise ValueError("nproc_per_node shouldn't be greater than 1 when processing data")
    else:
        raise ValueError("Please check your parameters!")