import mmengine
import numpy as np
import torch
import torch_sdaa
import torch.nn.functional as F
from mmengine.model import BaseModel
from mmengine.runner.checkpoint import _load_checkpoint

from mmagic.registry import MODELS
from mmagic.utils.typing import ForwardInputs


@MODELS.register_module()
class FlowStyleVTON(BaseModel):

    def __init__(self, warp_model, gen_model, pretrained_cfgs=None):
        super().__init__()
        self.warp_model = MODELS.build(warp_model)
        self.gen_model = MODELS.build(gen_model)
        if pretrained_cfgs:
            self.load_pretrained_models(pretrained_cfgs)

    def load_pretrained_models(self, pretrained_cfgs):
        """_summary_

        Args:
            pretrained_cfgs (_type_): _description_
        """
        for key, ckpt_cfg in pretrained_cfgs.items():
            map_location = ckpt_cfg.get('map_location', 'cpu')
            strict = ckpt_cfg.get('strict', False)
            ckpt_path = ckpt_cfg.get('ckpt_path')
            state_dict = _load_checkpoint(ckpt_path, map_location)
            getattr(self, key).load_state_dict(state_dict, strict=strict)
            mmengine.print_log(f'Load pretrained {key} from {ckpt_path}')

    @property
    def device(self):
        """Get current device of the model.

        Returns:
            torch.device: The current device of the model.
        """
        return next(self.parameters()).device

    def infer(self, inputs):

        real_image = inputs['image']
        clothes = inputs['clothes']
        edge = inputs['edge']
        edge = torch.FloatTensor(
            (edge.detach().numpy() > 0.5).astype(np.int64))
        clothes = clothes * edge

        device = self.device
        real_image, clothes, edge = real_image.to(device), clothes.to(
            device), edge.to(device)
        warped_cloth, last_flow = self.warp_model(real_image, clothes)
        warped_edge = F.grid_sample(
            edge,
            last_flow.permute(0, 2, 3, 1),
            mode='bilinear',
            padding_mode='zeros')

        gen_inputs = torch.cat([real_image, warped_cloth, warped_edge], 1)
        gen_outputs = self.gen_model(gen_inputs)
        p_rendered, m_composite = torch.split(gen_outputs, [3, 1], 1)
        p_rendered = torch.tanh(p_rendered)
        m_composite = torch.sigmoid(m_composite)
        m_composite = m_composite * warped_edge
        p_tryon = warped_cloth * m_composite + p_rendered * (1 - m_composite)

        flow_offset = de_offset(last_flow)
        flow_color = flow2color()(flow_offset)
        combine = torch.cat([
            real_image[0], clothes[0],
            flow_color.to(device),
            warped_cloth.to(device)[0], p_tryon[0]
        ], 2).squeeze()
        return p_tryon, combine

    def forward(self, inputs: ForwardInputs):
        return self.infer(inputs)


def make_colorwheel():
    """Generates a color wheel for optical flow visualization as presented in:

    Baker et al. "A Database and Evaluation Methodology for Optical Flow"
    (ICCV, 2007) URL: http://vision.middlebury.edu/flow/flowEval-iccv07.pdf
    According to the C++ source code of Daniel Scharstein
    According to the Matlab source code of Deqing Sun
    """
    RY = 15
    YG = 6
    GC = 4
    CB = 11
    BM = 13
    MR = 6

    ncols = RY + YG + GC + CB + BM + MR
    colorwheel = np.zeros((ncols, 3))
    col = 0

    # RY
    colorwheel[0:RY, 0] = 255
    colorwheel[0:RY, 1] = np.floor(255 * np.arange(0, RY) / RY)
    col = col + RY
    # YG
    colorwheel[col:col + YG, 0] = 255 - np.floor(255 * np.arange(0, YG) / YG)
    colorwheel[col:col + YG, 1] = 255
    col = col + YG
    # GC
    colorwheel[col:col + GC, 1] = 255
    colorwheel[col:col + GC, 2] = np.floor(255 * np.arange(0, GC) / GC)
    col = col + GC
    # CB
    colorwheel[col:col + CB, 1] = 255 - np.floor(255 * np.arange(CB) / CB)
    colorwheel[col:col + CB, 2] = 255
    col = col + CB
    # BM
    colorwheel[col:col + BM, 2] = 255
    colorwheel[col:col + BM, 0] = np.floor(255 * np.arange(0, BM) / BM)
    col = col + BM
    # MR
    colorwheel[col:col + MR, 2] = 255 - np.floor(255 * np.arange(MR) / MR)
    colorwheel[col:col + MR, 0] = 255
    return colorwheel


class flow2color():
    # code from: https://github.com/tomrunia/OpticalFlow_Visualization
    # MIT License
    #
    # Copyright (c) 2018 Tom Runia
    #
    # Permission is hereby granted, free of charge, to any person obtaining
    # a copy of this software and associated documentation files
    # (the "Software"), to deal in the Software without restriction,
    # including without limitation the rights to use, copy, modify, merge,
    # publish, distribute, sublicense, and/or sell copies of the Software,
    # and to permit persons to whom the Software is
    # furnished to do so, subject to conditions.
    #
    # Author: Tom Runia
    # Date Created: 2018-08-03
    def __init__(self):
        self.colorwheel = make_colorwheel()

    def flow_compute_color(self, u, v, convert_to_bgr=False):
        """Applies the flow color wheel to (possibly clipped) flow components u
        and v. According to the C++ source code of Daniel Scharstein According
        to the Matlab source code of Deqing Sun.

        :param u: np.ndarray, input horizontal flow
        :param v: np.ndarray, input vertical flow
        :param convert_to_bgr: bool, whether to change ordering and output BGR
         instead of RGB
        :return:
        """
        flow_image = np.zeros((u.shape[0], u.shape[1], 3), np.uint8)
        ncols = self.colorwheel.shape[0]

        rad = np.sqrt(np.square(u) + np.square(v))
        a = np.arctan2(-v, -u) / np.pi
        fk = (a + 1) / 2 * (ncols - 1)
        k0 = np.floor(fk).astype(np.int32)
        k1 = k0 + 1
        k1[k1 == ncols] = 0
        f = fk - k0

        for i in range(self.colorwheel.shape[1]):

            tmp = self.colorwheel[:, i]
            col0 = tmp[k0] / 255.0
            col1 = tmp[k1] / 255.0
            col = (1 - f) * col0 + f * col1

            idx = (rad <= 1)
            col[idx] = 1 - rad[idx] * (1 - col[idx])
            col[~idx] = col[~idx] * 0.75  # out of range?

            # Note the 2-i => BGR instead of RGB
            ch_idx = 2 - i if convert_to_bgr else i
            flow_image[:, :, ch_idx] = np.floor(255 * col)

        return flow_image

    def __call__(self, flow_uv, clip_flow=None, convert_to_bgr=False):
        """Expects a two dimensional flow image of shape [H,W,2] According to
        the C++ source code of Daniel Scharstein According to the Matlab source
        code of Deqing Sun.

        :param flow_uv: np.ndarray of shape [H,W,2]
        :param clip_flow: float, maximum clipping value for flow
        :return:
        """
        if len(flow_uv.size()) != 3:
            flow_uv = flow_uv[0]
        flow_uv = flow_uv.permute(1, 2, 0).cpu().detach().numpy()

        assert flow_uv.ndim == 3, 'input flow must have three dimensions'
        assert flow_uv.shape[2] == 2, 'input flow must have shape [H,W,2]'

        if clip_flow is not None:
            flow_uv = np.clip(flow_uv, 0, clip_flow)

        u = flow_uv[:, :, 1]
        v = flow_uv[:, :, 0]

        rad = np.sqrt(np.square(u) + np.square(v))
        rad_max = np.max(rad)

        epsilon = 1e-5
        u = u / (rad_max + epsilon)
        v = v / (rad_max + epsilon)
        image = self.flow_compute_color(u, v, convert_to_bgr)
        image = torch.tensor(image).float().permute(2, 0, 1) / 255.0 * 2 - 1
        return image


def de_offset(s_grid):
    [b, _, h, w] = s_grid.size()

    x = torch.arange(w).view(1, -1).expand(h, -1).float()
    y = torch.arange(h).view(-1, 1).expand(-1, w).float()
    x = 2 * x / (w - 1) - 1
    y = 2 * y / (h - 1) - 1
    grid = torch.stack([x, y], dim=0).float().sdaa()
    grid = grid.unsqueeze(0).expand(b, -1, -1, -1)

    offset = grid - s_grid

    offset_x = offset[:, 0, :, :] * (w - 1) / 2
    offset_y = offset[:, 1, :, :] * (h - 1) / 2

    offset = torch.cat((offset_y, offset_x), 0)

    return offset
