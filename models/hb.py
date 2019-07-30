import torch
import su2
import ipeps
from ctm.generic.env import ENV
from ctm.generic import rdm
from args import GLOBALARGS
from math import sqrt
import itertools

class HB():
    def __init__(self, global_args=GLOBALARGS()):
        self.dtype=global_args.dtype
        self.device=global_args.device
        self.phys_dim=2
        
        self.h = self.get_h()
        self.obs_ops = self.get_obs_ops()

    # build spin-1/2 coupled-ladders Hamiltonian
    # H = \sum_{<i,j>} h_i,j
    #  
    # y\x
    #    _:__:__:__:_
    # ..._|__|__|__|_...
    # ..._|__|__|__|_...
    # ..._|__|__|__|_...
    # ..._|__|__|__|_...
    # ..._|__|__|__|_...
    #     :  :  :  : 
    # 
    # where h_ij = S_i.S_j, indices of h correspond to s_i,s_j;s_i',s_j'
    def get_h(self):
        s2 = su2.SU2(self.phys_dim, dtype=self.dtype, device=self.device)
        expr_kron = 'ij,ab->iajb'
        SS = torch.einsum(expr_kron,s2.SZ(),s2.SZ()) + 0.5*(torch.einsum(expr_kron,s2.SP(),s2.SM()) \
            + torch.einsum(expr_kron,s2.SM(),s2.SP()))
        return SS

    def get_obs_ops(self):
        obs_ops = dict()
        s2 = su2.SU2(self.phys_dim, dtype=self.dtype, device=self.device)
        obs_ops["sz"]= s2.SZ()
        obs_ops["sp"]= s2.SP()
        obs_ops["sm"]= s2.SM()
        return obs_ops


    # evaluation of energy depends on the nature of underlying
    # ipeps state
    #
    # Ex.1 for 1-site c4v invariant iPEPS there is just a single 2site
    # operator which gives the energy-per-site
    #
    # Ex.2 for 1-site invariant iPEPS there are two two-site terms
    # which give the energy-per-site
    #    0       0
    # 1--A--3 1--A--3 
    #    2       2                          A
    #    0       0                          2
    # 1--A--3 1--A--3                       0
    #    2       2    , terms A--3 1--A and A have to be evaluated
    #
    # Ex.3 for 2x2 cluster iPEPS there are eight two-site terms
    #    0       0       0
    # 1--A--3 1--B--3 1--A--3
    #    2       2       2
    #    0       0       0
    # 1--C--3 1--D--3 1--C--3
    #    2       2       2             A--3 1--B      A B C D
    #    0       0                     B--3 1--A      2 2 2 2
    # 1--A--3 1--B--3                  C--3 1--D      0 0 0 0
    #    2       2             , terms D--3 1--C and  C D A B
    def energy_1x1c4v(self,ipeps):
        pass

    def energy_2x2(self,ipeps):
        pass

    # assuming reduced density matrix of 2x2 cluster with indexing of DOFs
    # as follows rdm2x2=rdm2x2(s0,s1,s2,s3;s0',s1',s2',s3')
    def energy_2x2(self,state,env):
        rdm2x2= rdm.rdm2x2((0,0),state,env)
        energy= torch.einsum('ijklabkl,ijab',rdm2x2,self.h)
        return energy

    def energy_2x1_1x2(self,state,env):
        energy=0.
        for coord,site in state.sites.items():
            rdm2x1 = rdm.rdm2x1(coord,state,env)
            rdm1x2 = rdm.rdm1x2(coord,state,env)
            energy += torch.einsum('ijab,ijab',rdm2x1,self.h)
            energy += torch.einsum('ijab,ijab',rdm1x2,self.h)

        # return energy-per-site
        energy_per_site=energy/len(state.sites.items())
        return energy_per_site

    def eval_obs(self,state,env):
        obs= dict({"avg_m": 0.})
        with torch.no_grad():
            for coord,site in state.sites.items():
                rdm1x1 = rdm.rdm1x1(coord,state,env)
                for label,op in self.obs_ops.items():
                    obs[f"{label}{coord}"]= torch.trace(rdm1x1@op)
                obs[f"m{coord}"]= sqrt(abs(obs[f"sz{coord}"]**2 + obs[f"sp{coord}"]*obs[f"sm{coord}"]))
                obs["avg_m"] += obs[f"m{coord}"]
            obs["avg_m"]= obs["avg_m"]/len(state.sites.keys())
        
        # prepare list with labels and values
        obs_labels=["avg_m"]+[f"m{coord}" for coord in state.sites.keys()]\
            +[f"{lc[1]}{lc[0]}" for lc in list(itertools.product(state.sites.keys(), self.obs_ops.keys()))]
        obs_values=[obs[label] for label in obs_labels]
        return obs_values, obs_labels