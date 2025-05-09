# Copyright (c) OpenMMLab. All rights reserved.
import torch
import torch_sdaa

from mmagic.models.editors import TOFlowVFINet


def test_tof_vfi_net():

    model = TOFlowVFINet()

    # test attributes
    assert model.__class__.__name__ == 'TOFlowVFINet'

    # prepare data
    inputs = torch.rand(1, 2, 3, 256, 256)

    # test on cpu
    output = model(inputs)
    assert torch.is_tensor(output)
    assert output.shape == (1, 3, 256, 256)

    # test on gpu
    if torch.sdaa.is_available():
        model = model.sdaa()
        inputs = inputs.sdaa()
        output = model(inputs)
        assert torch.is_tensor(output)
        assert output.shape == (1, 3, 256, 256)


def teardown_module():
    import gc
    gc.collect()
    globals().clear()
    locals().clear()
