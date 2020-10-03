from itertools import product
import json
import numpy as np

# assume bare tensor is defined as JSON object with
# 
#      key type content
# i)   dtype [str] datatype 
# ii)  dims  [list[int]] integer array of dimensions
# iii) data  [list[str]] 1D array of elements as strings
#
# in order to represent both real and complex floats.
# The immediate representation is by default in Numpy.
#
def read_bare_json_tensor(json_obj,backend="numpy"):
    t=None
    if backend=="numpy":
        t= read_bare_json_tensor_np(json_obj)
    else:
        raise Exception(f"Unsupported backend: {backend}")

    return t

def read_bare_json_tensor_np(json_obj):
    dtype_str= json_obj["dtype"].lower()
    assert dtype_str in ["float64","complex128"], "Invalid dtype"+dtype_str
    dims= json_obj["dims"]
    raw_data= json_obj["data"]

    # convert raw_data list[str] into list[dtype]
    if "complex" in dtype_str:
        raw_data= np.asarray(raw_data, dtype=np.complex128)
    else:
        raw_data= np.asarray(raw_data, dtype=np.float64)

    return raw_data.reshape(dims)

def read_bare_json_tensor_np_legacy(json_obj):
    t= json_obj
    # 0) find dtype, else assume float64
    dtype_str= json_obj["dtype"].lower() if "dtype" in t.keys() else "float64"
    assert dtype_str in ["float64","complex128"], "Invalid dtype"+dtype_str

    # 1) find the dimensions of indices
    if "dims" in t.keys():
        dims= t["dims"]
    else:
        # assume all auxiliary indices have the same dimension
        dims= [t["physDim"], t["auxDim"], t["auxDim"], \
            t["auxDim"], t["auxDim"]]

    X= np.zeros(dims, dtype=dtype_str)

    # 1) fill the tensor with elements from the list "entries"
    # which list the non-zero tensor elements in the following
    # notation. Dimensions are indexed starting from 0
    # 
    # index (integer) of physDim, left, up, right, down, (float) Re, Im
    #                             (or generic auxilliary inds ...)  
    if dtype_str=="complex128":
        for entry in t["entries"]:
            l = entry.split()
            X[tuple(int(i) for i in l[:-2])]=float(l[-2])+float(l[-1])*1.0j
    else:
        for entry in t["entries"]:
            l= entry.split()
            k= 1 if len(l)==len(dims)+1 else 2
            X[tuple(int(i) for i in l[:-k])]+=float(l[-k])
    
    return X

# assume abelian block as JSON object is composed of bare tensor
# with additional charge data
# 
#      key type content
# i)   charges [list[int]] list of charges
#
def read_json_abelian_block_legacy(json_obj,backend="numpy"):
    charges= json_obj["charges"]
    bare_t= read_bare_json_tensor_np_legacy(json_obj)
    assert len(charges)==len(bare_t.shape), f"Number of charges {len(charges)} incompatible "\
        +f"with bare tensor rank {len(bare_t.shape)}"
    return charges, bare_t

# assume abelian tensor is defined as JSON object with
#
#      key type content 
# i)   rank      [int] rank of tensor (number of dimensions)
# ii)  signature [list[int]] direction of indices (+1 or -1)
# iii) symmetry  [str] abelian group
# iv)  n         [int] total charge
# v)   isdiag    [bool] diagonal tensor
# vi)  dtype     [str] datatype
# vii) blocks    [list[abelian_block]] list of blocks
#
# NOTE: the default settings (abelian group, backend) of Tensor are injected 
#       by tensor_abelian module 
#
def read_json_abelian_tensor_legacy(json_obj,config=None):
    r"""
    :param json_obj: dictionary from parsed json file
    :type json_obj: dict
    """

    # TODO validation
    s= json_obj["signature"]
    symmetry= json_obj["symmetry"]
    n= json_obj["n"]
    isdiag= json_obj["isdiag"]
    dtype_str= json_obj["dtype"].lower()
    assert dtype_str in ["float64","complex128"], "Invalid dtype"+dtype_str

    # create empty abelian tensor
    T= Tensor(s=s, n=0, isdiag=isdiag, dtype= dtype_str)
    # TODO assign symmetry in constructor or settings ?
    if symmetry!=T.symmetry:
        warnings.warn(f"Incompatible tensor symmetry: Expected {T.symmetry}, read {symmetry}")
    T.symmetry= symmetry

    # parse blocks
    for b in json_obj["blocks"]:
        charges, bare_b= read_json_abelian_block_legacy(b)
        T.set_block(ts=charges, val=bare_b)

    return T

def serialize_bare_tensor_np(t):
    json_tensor=dict()

    dtype_str= f"{t.dtype}"
    if dtype_str.find(".")>0:
        dtype_str= dtype_str[dtype_str.find(".")+1:]

    json_tensor["format"]= "1D"
    json_tensor["dtype"]= dtype_str
    json_tensor["dims"]= list(t.size())

    json_tensor["data"]= []
    t_1d= t.view(-1)
    for i in range(t.numel()):
        json_tensor["data"].append(f"{t_1d[i]}")
    return json_tensor

def serialize_bare_tensor_legacy(t):
    json_tensor=dict()

    dtype_str= f"{t.dtype}"
    if dtype_str.find(".")>0:
        dtype_str= dtype_str[dtype_str.find(".")+1:]

    json_tensor["dtype"]= dtype_str
    json_tensor["dims"]= list(t.size())

    tlength = t.numel()
    json_tensor["numEntries"]= tlength
    
    entries = []
    elem_inds = list(product( *(range(i) for i in t.size()) ))
    if t.is_complex():
        for ei in elem_inds:
            entries.append(" ".join([f"{i}" for i in ei])\
                +f" {t[ei].real} {t[ei].imag}")
    else:
        for ei in elem_inds:
            entries.append(" ".join([f"{i}" for i in ei])\
                +f" {t[ei]}")

    json_tensor["entries"]=entries
    return json_tensor

def serialize_abelian_tensor(t):
    json_tensor=dict()

    json_tensor["format"]= "abelian"
    json_tensor["rank"]= s._ndim
    json_tensor["signature"]= list(t.s)
    #json_tensor["symmetry"]= 
    json_tensor["n"]= list(t.n)
    json_tensor["isdiag"]= t.isdiag
    json_tensor["dtype"]= t.dtype

    json_tensor["blocks"]= []
    for b in t.A:
        json_tensor["blocks"].append(serialize_bare_tensor_legacy(b))

    return json_tensor
