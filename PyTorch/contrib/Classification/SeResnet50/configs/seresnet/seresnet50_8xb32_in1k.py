default_scope = 'mmpretrain'
_base_ = [
    '../_base_/models/seresnet50.py',
    '../_base_/datasets/imagenet_bs32_pil_resize.py',
    '../_base_/schedules/imagenet_bs256_140e.py',
    '../_base_/default_runtime.py'
]
