# Copyright (c) OpenMMLab. All rights reserved.
import torch
import torch_sdaa

from mmagic.models.archs import PixelShufflePack


def test_pixel_shuffle():

    # test on cpu
    model = PixelShufflePack(3, 3, 2, 3)
    model.init_weights()
    x = torch.rand(1, 3, 16, 16)
    y = model(x)
    assert y.shape == (1, 3, 32, 32)

    # test on gpu
    if torch.sdaa.is_available():
        model = model.sdaa()
        x = x.sdaa()
        y = model(x)
        assert y.shape == (1, 3, 32, 32)


def teardown_module():
    import gc
    gc.collect()
    globals().clear()
    locals().clear()
