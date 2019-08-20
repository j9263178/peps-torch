import time
import torch
from torch.utils.checkpoint import checkpoint
import config as cfg
import ipeps
from ipeps import IPEPS
from ctm.generic.env import *
from ctm.generic.ctm_components import *
from ctm.generic.ctm_projectors import *

def run(state, env, conv_check=None, ctm_args=cfg.ctm_args, global_args=cfg.global_args): 
    r"""
    :param state: wavefunction
    :param env: environment
    :param conv_check: function which determines the convergence of CTM algorithm. If ``None``,
                       the algorithm performs ``ctm_args.ctm_max_iter`` iterations. 
    :param ctm_args: CTM algorithm configuration
    :param global_args: global configuration
    :type state: IPEPS
    :type env: ENV
    :type conv_check: function(IPEPS,ENV,list[float],CTMARGS)->bool
    :type ctm_args: CTMARGS
    :type global_args: GLOBALARGS

    Executes directional CTM algorithm for generic iPEPS starting from the intial environment ``env``.
    TODO add reference
    """

    # 0) Create double-layer (DL) tensors, preserving the same convenction
    # for order of indices 
    #
    #     /           /
    #  --A^dag-- = --a--
    #   /|          /
    #    |/
    #  --A--
    #   /
    #
    sitesDL=dict()
    for coord,A in state.sites.items():
        dimsA = A.size()
        a = torch.einsum('mefgh,mabcd->eafbgchd',(A,A)).contiguous().view(dimsA[1]**2,\
            dimsA[2]**2, dimsA[3]**2, dimsA[4]**2)
        sitesDL[coord]=a
    stateDL = IPEPS(sitesDL,state.vertexToSite)

    # 1) perform CTMRG
    t0= time.perf_counter()
    history=[]
    for i in range(ctm_args.ctm_max_iter):
        for direction in ctm_args.ctm_move_sequence:
            ctm_MOVE(direction, stateDL, env, ctm_args=ctm_args, global_args=global_args, verbosity=ctm_args.verbosity_ctm_move)

        if conv_check is not None:
            # evaluate convergence of the CTMRG procedure
            converged = conv_check(state, env, history, ctm_args=ctm_args)
            if ctm_args.verbosity_ctm_convergence>1: print(history[-1])
            if converged:
                if ctm_args.verbosity_ctm_convergence>0: 
                    print(f"CTMRG  converged at iter= {i}, history= {history[-1]}")
                break
    t1= time.perf_counter()

    return env, history, t1-t0

# performs CTM move in one of the directions 
# [Up=(0,-1), Left=(-1,0), Down=(0,1), Right=(1,0)]
def ctm_MOVE(direction, state, env, ctm_args=cfg.ctm_args, global_args=cfg.global_args, verbosity=0):
    # Loop over all non-equivalent sites of ipeps
    # and compute projectors P(coord), P^tilde(coord)
    if ctm_args.projector_method=='4X4':
        ctm_get_projectors=ctm_get_projectors_4x4
    elif ctm_args.projector_method=='4X2':
        ctm_get_projectors=ctm_get_projectors_4x2
    else:
        raise ValueError("Invalid Projector method: "+str(ctm_args.projector_method))
    P = dict()
    Pt = dict()
    for coord,site in state.sites.items():
        # TODO compute isometries
        P[coord], Pt[coord] = ctm_get_projectors(direction, coord, state, env, ctm_args, global_args)
        if verbosity>0:
            print("P,Pt RIGHT "+str(coord)+" P: "+str(P[coord].size())+" Pt: "+str(Pt[coord].size()))
        if verbosity>1:
            print(P[coord])
            print(Pt[coord])

    # Loop over all non-equivalent sites of ipeps
    # and perform absorption and truncation
    nC1 = dict()
    nC2 = dict()
    nT = dict()
    for coord,site in state.sites.items():
        if direction==(0,-1):
            nC1[coord], nC2[coord], nT[coord] = absorb_truncate_CTM_MOVE_UP(coord, state, env, P, Pt)
        elif direction==(-1,0):
            nC1[coord], nC2[coord], nT[coord] = absorb_truncate_CTM_MOVE_LEFT(coord, state, env, P, Pt)
        elif direction==(0,1):
            nC1[coord], nC2[coord], nT[coord] = absorb_truncate_CTM_MOVE_DOWN(coord, state, env, P, Pt)
        elif direction==(1,0):
            nC1[coord], nC2[coord], nT[coord] = absorb_truncate_CTM_MOVE_RIGHT(coord, state, env, P, Pt)
        else:
            raise ValueError("Invalid direction: "+str(direction))

    rel_CandT_vecs = dict()
    # specify relative vectors identifying the environment tensors
    # with respect to the direction 
    if direction==(0,-1):
        rel_CandT_vecs = {"nC1": (1,-1), "nC2": (-1,-1), "nT": direction}
    elif direction==(-1,0):
        rel_CandT_vecs = {"nC1": (-1,-1), "nC2": (-1,1), "nT": direction}
    elif direction==(0,1):
        rel_CandT_vecs = {"nC1": (-1,1), "nC2": (1,1), "nT": direction}
    elif direction==(1,0):
        rel_CandT_vecs = {"nC1": (1,1), "nC2": (1,-1), "nT": direction}
    else:
        raise ValueError("Invalid direction: "+str(direction))

    # Assign new nC1,nT,nC2 to appropriate environment tensors
    for coord,site in state.sites.items():
        new_coord = state.vertexToSite((coord[0]-direction[0], coord[1]-direction[1]))
        # print("coord: "+str(coord)+" + "+str(direction)+" -> "+str(new_coord))
        
        env.C[(new_coord,rel_CandT_vecs["nC1"])] = nC1[coord]
        env.C[(new_coord,rel_CandT_vecs["nC2"])] = nC2[coord]
        env.T[(new_coord,rel_CandT_vecs["nT"])] = nT[coord]

#####################################################################
# functions performing absorption and truncation step
#####################################################################
def absorb_truncate_CTM_MOVE_UP(coord, ipeps, env, P, Pt, verbosity=0):
    vec = (1,0)
    coord_shift_right = ipeps.vertexToSite((coord[0]+vec[0], coord[1]+vec[1]))
    tensors= env.C[(coord,(1,-1))], env.T[(coord,(1,0))], env.T[(coord,(0,-1))], \
        env.T[(coord,(-1,0))], env.C[(coord,(-1,-1))], ipeps.site(coord), \
        P[coord].view(env.chi,ipeps.site(coord).size()[3],env.chi), \
        Pt[coord].view(env.chi,ipeps.site(coord).size()[1],env.chi), \
        P[coord_shift_right].view(env.chi,ipeps.site(coord).size()[3],env.chi), \
        Pt[coord_shift_right].view(env.chi,ipeps.site(coord).size()[1],env.chi)

    if cfg.ctm_args.fwd_checkpoint_absorb:
        return checkpoint(absorb_truncate_CTM_MOVE_UP_c,*tensors)
    else:
        return absorb_truncate_CTM_MOVE_UP_c(*tensors)

def absorb_truncate_CTM_MOVE_UP_c(*tensors):
    C1, T1, T, T2, C2, A, P2, Pt2, P1, Pt1= tensors

    # 0--C1
    #    1
    #    0
    # 1--T1
    #    2 
    nC1 = torch.tensordot(C1,T1,([1],[0]))

    #        --0 0--C1
    #       |       |
    # 0<-2--Pt1     |
    #       |       | 
    #        --1 1--T1
    #               2->1
    nC1 = torch.tensordot(Pt1, nC1,([0,1],[0,1]))

    # C2--1->0
    # 0
    # 0
    # T2--2
    # 1
    nC2 = torch.tensordot(C2, T2,([0],[0])) 

    # C2--0 0--
    # |        |        
    # |        P2--2->1
    # |        |
    # T2--2 1--
    # 1->0
    nC2 = torch.tensordot(nC2, P2,([0,2],[0,1]))

    #        --0 0--T--2->3
    #       |       1->2
    # 1<-2--Pt2
    #       |
    #        --1->0 
    nT = torch.tensordot(Pt2, T, ([0],[0]))

    #        -------T--3->1
    #       |       2
    # 0<-1--Pt2     | 
    #       |       0
    #        --0 1--A--3
    #               2 
    nT = torch.tensordot(nT, A,([0,2],[1,0]))

    #     -------T--1 0--
    #    |       |       |
    # 0--Pt2     |       P1--2
    #    |       |       |
    #     -------A--3 1--
    #            2->1 
    nT = torch.tensordot(nT, P1,([1,3],[0,1]))
    nT = nT.contiguous()

    # Assign new C,T 
    #
    # C(coord,(-1,-1))--                --T(coord,(0,-1))--             --C(coord,(1,-1))
    # |                  P2--       --Pt2 |                P1--     -Pt1  |
    # T(coord,(-1,0))---                --A(coord)---------             --T(coord,(1,0))
    # |                                   |                               |
    #
    # =>                            
    #
    # C^new(coord+(0,1),(-1,-1))--      --T^new(coord+(0,1),(0,-1))--   --C^new(coord+(0,1),(1,-1))
    # |                                   |                               |  
    # vec = (0,1)
    # new_coord = ipeps.vertexToSite((coord[0]+vec[0], coord[1]+vec[1]))
    # print("coord: "+str(coord)+" + "+str(vec)+" -> "+str(new_coord))
    # env.C[(new_coord,(1,-1))] = nC1/torch.max(torch.abs(nC1))
    # env.C[(new_coord,(-1,-1))] = nC2/torch.max(torch.abs(nC2))
    # env.T[(new_coord,(0,-1))] = nT/torch.max(torch.abs(nT))
    nC1 = nC1/torch.max(torch.abs(nC1))
    nC2 = nC2/torch.max(torch.abs(nC2))
    nT = nT/torch.max(torch.abs(nT))
    return nC1, nC2, nT

def absorb_truncate_CTM_MOVE_LEFT(coord, ipeps, env, P, Pt, verbosity=0):
    vec = (0,-1)
    coord_shift_up = ipeps.vertexToSite((coord[0]+vec[0], coord[1]+vec[1]))
    tensors = env.C[(coord,(-1,-1))], env.T[(coord,(0,-1))], env.T[(coord,(-1,0))], \
        env.T[(coord,(0,1))], env.C[(coord,(-1,1))], ipeps.site(coord), \
        P[coord].view(env.chi,ipeps.site(coord).size()[0],env.chi), \
        Pt[coord].view(env.chi,ipeps.site(coord).size()[2],env.chi), \
        P[coord_shift_up].view(env.chi,ipeps.site(coord).size()[0],env.chi), \
        Pt[coord_shift_up].view(env.chi,ipeps.site(coord).size()[2],env.chi)

    if cfg.ctm_args.fwd_checkpoint_absorb:
        return checkpoint(absorb_truncate_CTM_MOVE_LEFT_c,*tensors)
    else:
        return absorb_truncate_CTM_MOVE_LEFT_c(*tensors)

def absorb_truncate_CTM_MOVE_LEFT_c(*tensors):
    C1, T1, T, T2, C2, A, P2, Pt2, P1, Pt1= tensors

    # C1--1 0--T1--2
    # |        |
    # 0        1
    nC1 = torch.tensordot(C1,T1,([1],[0]))

    # C1--1 0--T1--2->1
    # |        |
    # 0        1
    # 0        1
    # |___Pt1__|
    #     2->0
    nC1 = torch.tensordot(Pt1, nC1,([0,1],[0,1]))

    # 0        0->1
    # C2--1 1--T2--2
    nC2 = torch.tensordot(C2, T2,([1],[1])) 

    #    2->0
    # ___P2___
    # 0      1
    # 0      1  
    # C2-----T2--2->1
    nC2 = torch.tensordot(P2, nC2,([0,1],[0,1]))

    #    2->1
    # ___P1__
    # 0     1->0
    # 0
    # T--2->3
    # 1->2
    nT = torch.tensordot(P1, T,([0],[0]))

    #    1->0
    # ___P1____
    # |       0
    # |       0
    # T--3 1--A--3
    # 2->1    2
    nT = torch.tensordot(nT, A,([0,3],[0,1]))

    #    0
    # ___P1___
    # |       |
    # |       |
    # T-------A--3->1
    # 1       2
    # 0       1
    # |___Pt2_|
    #     2
    nT = torch.tensordot(nT, Pt2,([1,2],[0,1]))
    nT = nT.permute(0,2,1).contiguous()

    # Assign new C,T 
    #
    # C(coord,(-1,-1))--T(coord,(0,-1))-- => C^new(coord+(1,0),(-1,-1))--
    # |________   ______|                    |
    #          Pt1
    #          |
    #
    #          |
    # _________P1______
    # |                |                     |
    # T(coord,(-1,0))--A(coord)--            T^new(coord+(1,0),(-1,0))--
    # |________   _____|                     |
    #          Pt2
    #          |                     
    #          
    #          |
    #  ________P2_______
    # |                 |                    |
    # C(coord,(-1,1))--T(coord,(0,1))--      C^new(coord+(1,0),(-1,1))
    # vec = (1,0)
    # new_coord = ipeps.vertexToSite((coord[0]+vec[0], coord[1]+vec[1]))
    # print("coord: "+str(coord)+" + "+str(vec)+" -> "+str(new_coord))
    # env.C[(new_coord,(-1,-1))] = nC1/torch.max(torch.abs(nC1))
    # env.C[(new_coord,(-1,1))] = nC2/torch.max(torch.abs(nC2))
    # env.T[(new_coord,(-1,0))] = nT/torch.max(torch.abs(nT))
    nC1 = nC1/torch.max(torch.abs(nC1))
    nC2 = nC2/torch.max(torch.abs(nC2))
    nT = nT/torch.max(torch.abs(nT))
    return nC1, nC2, nT

def absorb_truncate_CTM_MOVE_DOWN(coord, ipeps, env, P, Pt, verbosity=0):
    vec = (-1,0)
    coord_shift_left = ipeps.vertexToSite((coord[0]+vec[0], coord[1]+vec[1]))
    tensors= env.C[(coord,(-1,1))], env.T[(coord,(-1,0))], env.T[(coord,(0,1))], \
        env.T[(coord,(1,0))], env.C[(coord,(1,1))], ipeps.site(coord), \
        P[coord].view(env.chi,ipeps.site(coord).size()[1],env.chi), \
        Pt[coord].view(env.chi,ipeps.site(coord).size()[3],env.chi), \
        P[coord_shift_left].view(env.chi,ipeps.site(coord).size()[1],env.chi), \
        Pt[coord_shift_left].view(env.chi,ipeps.site(coord).size()[3],env.chi)

    if cfg.ctm_args.fwd_checkpoint_absorb:
        return checkpoint(absorb_truncate_CTM_MOVE_DOWN_c,*tensors)
    else:
        return absorb_truncate_CTM_MOVE_DOWN_c(*tensors)

def absorb_truncate_CTM_MOVE_DOWN_c(*tensors):
    C1, T1, T, T2, C2, A, P2, Pt2, P1, Pt1= tensors

    # 0->1
    # T1--2->2
    # 1
    # 0
    # C1--1->0
    nC1 = torch.tensordot(C1,T1,([0],[1]))

    # 1->0
    # T1--2 1--
    # |        |        
    # |        Pt1--2->1
    # |        |
    # C1--0 0--   
    nC1 = torch.tensordot(nC1, Pt1, ([0,2],[0,1]))

    #    1<-0
    # 2<-1--T2
    #       2
    #       0
    # 0<-1--C2
    nC2 = torch.tensordot(C2, T2,([0],[2])) 

    #            0<-1
    #        --1 2--T2
    #       |       |
    # 1<-2--P2      |
    #       |       | 
    #        --0 0--C2
    nC2 = torch.tensordot(nC2, P2, ([0,2],[0,1]))

    #        --1->0
    #       |
    # 1<-2--P1
    #       |       0->2
    #        --0 1--T--2->3 
    nT = torch.tensordot(P1, T, ([0],[1]))

    #               0->2
    #        --0 1--A--3 
    #       |       2 
    # 0<-1--P1      |
    #       |       2
    #        -------T--3->1
    nT = torch.tensordot(nT, A,([0,2],[1,2]))

    #               2->1
    #        -------A--3 1--
    #       |       |       |
    #    0--P1      |       Pt2--2
    #       |       |       |
    #        -------T--1 0--
    nT = torch.tensordot(nT, Pt2,([1,3],[0,1]))
    nT = nT.permute(1,0,2).contiguous()

    # Assign new C,T
    # 
    # |                                 |                              |
    # T(coord,(-1,0))--               --A(coord)--------             --T(coord,(1,0))
    # |                Pt1--      --P1  |               Pt2--    --P2  |
    # C(coord,(-1,1))--               --T(coord,(0,1))--             --C(coord,(1,1))
    #
    # =>                            
    #
    # |                                 |                              |
    # C^new(coord+(0,-1),(-1,1))--    --T^new(coord+(0,-1),(0,1))--  --C^new(coord+(0,-1),(1,1))
    # vec = (0,-1)
    # new_coord = ipeps.vertexToSite((coord[0]+vec[0], coord[1]+vec[1]))
    # print("coord: "+str(coord)+" + "+str(vec)+" -> "+str(new_coord))
    # env.C[(new_coord,(-1,1))] = nC1/torch.max(torch.abs(nC1))
    # env.C[(new_coord,(1,1))] = nC2/torch.max(torch.abs(nC2))
    # env.T[(new_coord,(0,1))] = nT/torch.max(torch.abs(nT))
    nC1 = nC1/torch.max(torch.abs(nC1))
    nC2 = nC2/torch.max(torch.abs(nC2))
    nT = nT/torch.max(torch.abs(nT))
    return nC1, nC2, nT

def absorb_truncate_CTM_MOVE_RIGHT(coord, ipeps, env, P, Pt, verbosity=0):
    vec = (0,1)
    coord_shift_down = ipeps.vertexToSite((coord[0]+vec[0], coord[1]+vec[1]))
    tensors= env.C[(coord,(1,1))], env.T[(coord,(0,1))], env.T[(coord,(1,0))], \
        env.T[(coord,(0,-1))], env.C[(coord,(1,-1))], ipeps.site(coord), \
        P[coord].view(env.chi,ipeps.site(coord).size()[2],env.chi), \
        Pt[coord].view(env.chi,ipeps.site(coord).size()[0],env.chi), \
        P[coord_shift_down].view(env.chi,ipeps.site(coord).size()[2],env.chi), \
        Pt[coord_shift_down].view(env.chi,ipeps.site(coord).size()[0],env.chi)

    if cfg.ctm_args.fwd_checkpoint_absorb:
        return checkpoint(absorb_truncate_CTM_MOVE_RIGHT_c,*tensors)
    else:
        return absorb_truncate_CTM_MOVE_RIGHT_c(*tensors)

def absorb_truncate_CTM_MOVE_RIGHT_c(*tensors):
    C1, T1, T, T2, C2, A, P2, Pt2, P1, Pt1= tensors

    #       0->1     0
    # 2<-1--T1--2 1--C1
    nC1 = torch.tensordot(C1, T1,([1],[2])) 

    #          2->0
    #        __Pt1_
    #       1     0
    #       1     0
    # 1<-2--T1----C1
    nC1 = torch.tensordot(Pt1, nC1,([0,1],[0,1]))

    # 1<-0--T2--2 0--C2
    #    2<-1     0<-1
    nC2 = torch.tensordot(C2,T2,([0],[2]))

    # 0<-1--T2----C2
    #       2     0
    #       1     0
    #       |__P2_|
    #          2->1
    nC2 = torch.tensordot(nC2, P2,([0,2],[0,1]))

    #    1<-2
    #    ___Pt2__
    # 0<-1      0
    #           0
    #     2<-1--T
    #        3<-2
    nT = torch.tensordot(Pt2, T,([0],[0]))

    #       0<-1 
    #       ___Pt2__
    #       0       |
    #       0       |
    # 2<-1--A--3 2--T
    #    3<-2    1<-3
    nT = torch.tensordot(nT, A,([0,2],[0,3]))

    #          0
    #       ___Pt2__
    #       |       |
    #       |       |
    # 1<-2--A-------T
    #       3       1
    #       1       0
    #       |___P1__|
    #           2 
    nT = torch.tensordot(nT, P1,([1,3],[0,1]))
    nT = nT.contiguous()

    # Assign new C,T 
    #
    # --T(coord,(0,-1))--C(coord,(1,-1)) =>--C^new(coord+(-1,0),(1,-1))
    #   |______  ________|                   |
    #          P2
    #          |
    #
    #          |
    #    ______Pt2
    #   |         |                          |
    # --A(coord)--T(coord,(1,0))           --T^new(coord+(-1,0),(1,0))
    #   |______  _|                          |
    #          P1
    #          |                     
    #          
    #          |
    #    ______Pt1______
    #   |               |                    |
    # --T(coord,(0,1))--C(coord,(1,1))     --C^new(coord+(-1,0),(1,1))
    # vec = (-1,0)
    # new_coord = ipeps.vertexToSite((coord[0]+vec[0], coord[1]+vec[1]))
    # print("coord: "+str(coord)+" + "+str(vec)+" -> "+str(new_coord))
    # env.C[(new_coord,(1,1))] = nC1/torch.max(torch.abs(nC1))
    # env.C[(new_coord,(1,-1))] = nC2/torch.max(torch.abs(nC2))
    # env.T[(new_coord,(1,0))] = nT/torch.max(torch.abs(nT))
    nC1 = nC1/torch.max(torch.abs(nC1))
    nC2 = nC2/torch.max(torch.abs(nC2))
    nT = nT/torch.max(torch.abs(nT))
    return nC1, nC2, nT