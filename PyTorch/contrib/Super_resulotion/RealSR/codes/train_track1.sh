export SDAA_VISIBLE_DEVICES=0,1,2,3
python -m torch.distributed.launch --nproc_per_node=4 train.py -opt options/df2k/train_bicubic_noise.yml \
    --launcher pytorch \
    

# export SDAA_VISIBLE_DEVICES=0
# python3 train.py -opt options/df2k/train_bicubic_noise.yml \