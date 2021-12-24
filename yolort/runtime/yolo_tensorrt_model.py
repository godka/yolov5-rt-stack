# Copyright (c) 2021, yolort team. All Rights Reserved.
from pathlib import Path
from typing import Optional, Tuple, Union

import torch
from torch import nn, Tensor
from yolort.models import YOLO
from yolort.models.anchor_utils import AnchorGenerator
from yolort.models.backbone_utils import darknet_pan_backbone
from yolort.models.box_head import LogitsDecoder
from yolort.utils import load_from_ultralytics

__all__ = ["YOLOTRTModule"]


class YOLOTRTModule(nn.Module):
    """
    TensorRT deployment friendly wrapper for YOLO.

    Remove the ``torchvision::nms`` in this warpper, due to the fact that some third-party
    inference frameworks currently do not support this operator very well.
    """

    def __init__(
        self,
        checkpoint_path: str,
        version: str = "r6.0",
    ):
        super().__init__()
        model_info = load_from_ultralytics(checkpoint_path, version=version)

        backbone_name = f"darknet_{model_info['size']}_{version.replace('.', '_')}"
        depth_multiple = model_info["depth_multiple"]
        width_multiple = model_info["width_multiple"]
        use_p6 = model_info["use_p6"]
        backbone = darknet_pan_backbone(
            backbone_name,
            depth_multiple,
            width_multiple,
            version=version,
            use_p6=use_p6,
        )
        num_classes = model_info["num_classes"]
        anchor_generator = AnchorGenerator(model_info["strides"], model_info["anchor_grids"])
        post_process = LogitsDecoder(model_info["strides"])
        model = YOLO(
            backbone,
            num_classes,
            anchor_generator=anchor_generator,
            post_process=post_process,
        )

        model.load_state_dict(model_info["state_dict"])
        self.model = model

    @torch.no_grad()
    def forward(self, inputs: Tensor) -> Tuple[Tensor, Tensor]:
        """
        Args:
            inputs (Tensor): batched images, of shape [batch_size x 3 x H x W]
        """
        # Compute the detections
        outputs = self.model(inputs)

        return outputs

    @torch.no_grad()
    def to_onnx(
        self,
        file_path: Union[str, Path],
        input_sample: Optional[Tensor] = None,
        opset_version: int = 11,
        enable_dynamic: bool = True,
        **kwargs,
    ):
        """
        Saves the model in ONNX format.

        Args:
            file_path: The path of the file the onnx model should be saved to.
            input_sample: An input for tracing. Default: None.
            opset_version: Opset version we export the model to the onnx submodule. Default: 11.
            enable_dynamic: Whether to specify axes of tensors as dynamic. Default: True.
            **kwargs: Will be passed to torch.onnx.export function.
        """
        if input_sample is None:
            input_sample = torch.rand(1, 3, 320, 320).to(next(self.parameters()).device)

        dynamic_axes = (
            {
                "images_tensors": {0: "batch", 2: "height", 3: "width"},
                "boxes": {0: "batch", 1: "num_objects"},
                "scores": {0: "batch", 1: "num_objects"},
            }
            if enable_dynamic
            else None
        )

        input_names = ["images_tensors"]
        output_names = ["boxes", "scores"]

        torch.onnx.export(
            self.model,
            input_sample,
            file_path,
            do_constant_folding=True,
            opset_version=opset_version,
            input_names=input_names,
            output_names=output_names,
            dynamic_axes=dynamic_axes,
            **kwargs,
        )
