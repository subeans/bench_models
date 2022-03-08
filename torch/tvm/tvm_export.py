from json import load
import warnings
import time
import numpy as np
import tvm
from tvm import relay
import tvm.contrib.graph_executor as runtime
import torch

import argparse


def timer(thunk, repeat=1, number=10, dryrun=3, min_repeat_ms=1000):
    """Helper function to time a function"""
    for i in range(dryrun):
        thunk()
    ret = []
    for _ in range(repeat):
        while True:
            beg = time.time()
            for _ in range(number):
                thunk()
            end = time.time()
            lat = (end - beg) * 1e3
            if lat >= min_repeat_ms:
                break
            number = int(max(min_repeat_ms / (lat / number) + 1, number * 1.618))
        ret.append(lat / number)
    return ret

def load_model(model_name,batchsize):

    PATH = f"./{model_name}_{batchsize}/"
    model = torch.load(PATH + 'model.pt')  # 전체 모델을 통째로 불러옴, 클래스 선언 필수
    model.load_state_dict(torch.load(PATH + 'model_state_dict.pt'))  # state_dict를 불러 온 후, 모델에 저장

    return model 


def convert_to_nhwc(mod):
    """Convert to NHWC layout"""
    desired_layouts = {"nn.conv2d": ["NHWC", "default"]}
    seq = tvm.transform.Sequential(
        [
            relay.transform.RemoveUnusedFunctions(),
            relay.transform.ConvertLayout(desired_layouts),
        ]
    )
    with tvm.transform.PassContext(opt_level=3):
        mod = seq(mod)
    return mod


def compile_export(mod,params,target,batch_size):
    if target == "arm":
        target = tvm.target.arm_cpu()
    with tvm.transform.PassContext(opt_level=3):
        mod = relay.transform.InferType()(mod)
        lib = relay.build(mod, target=target, params=params)
    lib.export_library(f"./{model_name}_{batch_size}.tar")
    return lib 


def benchmark(model_name,imgsize,batch_size,target,dtype="float32",layout="NCHW"):
    input_name = "input0"
    input_shape = (batch_size, 3, imgsize, imgsize)

    # data = np.random.uniform(size=input_shape)
    data_array = np.random.uniform(0, 255, size=input_shape).astype("float32")
    torch_data = torch.tensor(data_array)

    model = load_model(model_name,batch_size)

    traced_model = torch.jit.trace(model, torch_data)

    # mxnet to tvm format

    mod, params = relay.frontend.from_pytorch(traced_model, input_infos=[('input0', input_shape)],default_dtype=dtype)

    if layout == "NHWC":
        mod = convert_to_nhwc(mod)
    else:
        assert layout == "NCHW"

    lib=compile_export(mod,params,target,batch_size)
    print("export done :",f"{model_name}_{batch_size}.tar")

    dev = tvm.cpu()
    module = runtime.GraphModule(lib["default"](dev))

    data = np.random.uniform(size=input_shape)
    module.set_input(input_name, data)

    # Evaluate
    ftimer = module.module.time_evaluator("run", dev, min_repeat_ms=500, repeat=5)
    prof_res = np.array(ftimer().results) * 1000
    print(f"TVM {model_name} latency for batch {batch_size} : {np.mean(prof_res):.2f} ms")
 


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--model',default='resnet50' , type=str)
    parser.add_argument('--batchsize',default=1 , type=int)
    parser.add_argument('--target',default='llvm -mcpu=core-avx2' , type=str)

    args = parser.parse_args()

    model_name = args.model
    batch_size = args.batchsize
    target = args.target

    img_size = 224
    
    if model_name == 'inception_v3':
        img_size = 299
    benchmark(model_name,img_size,batch_size,target)
