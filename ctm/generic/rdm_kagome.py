import torch
from ctm.generic.env import ENV
import ctm.generic.ctm_components as ctm_components
from ctm.generic.rdm import _sym_pos_def_rdm
from tn_interface import contract, einsum
from tn_interface import contiguous, view, permute
from tn_interface import conj

def rdm2x2_up_triangle_open(coord, state, env, sym_pos_def=False, force_cpu=False, verbosity=0):
    r"""
    :param coord: vertex (x,y) specifies upper left site of 2x2 subsystem
    :param state: underlying wavefunction
    :param env: environment corresponding to ``state``
    :param verbosity: logging verbosity
    :type coord: tuple(int,int)
    :type state: IPEPS
    :type env: ENV
    :type verbosity: int
    :rtype: torch.tensor
    """
    who = "rdm2x2"
    # ----- building C2x2_LU ----------------------------------------------------
    if force_cpu:
        C = env.C[(state.vertexToSite(coord), (-1, -1))].cpu()
        T1 = env.T[(state.vertexToSite(coord), (0, -1))].cpu()
        T2 = env.T[(state.vertexToSite(coord), (-1, 0))].cpu()
        a_1layer = state.site(coord).cpu()
    else:
        C = env.C[(state.vertexToSite(coord), (-1, -1))]
        T1 = env.T[(state.vertexToSite(coord), (0, -1))]
        T2 = env.T[(state.vertexToSite(coord), (-1, 0))]
        a_1layer = state.site(coord)
    dimsA = a_1layer.size()

    # contract all physical sites of this unit cell (index m)
    a = contiguous(einsum('mefgh,mabcd->eafbgchd', a_1layer, conj(a_1layer)))
    a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2))

    # C--10--T1--2
    # 0	     1
    C2x2_LU = contract(C, T1, ([1], [0]))

    # C------T1--2->1
    # 0	     1->0
    # 0
    # T2--2->3
    # 1->2
    C2x2_LU = contract(C2x2_LU, T2, ([0], [0]))

    # C-------T1--1->0
    # |	      0
    # |	      0
    # T2--3 1 a--3
    # 2->1	  2
    C2x2_LU = contract(C2x2_LU, a, ([0, 3], [0, 1]))

    # permute 0123->1203
    # reshape (12)(03)->01
    # C2x2--1
    # |
    # 0
    C2x2_LU = contiguous(permute(C2x2_LU, (1, 2, 0, 3)))
    C2x2_LU = view(C2x2_LU, (T2.size(1) * a.size(2), T1.size(2) * a.size(3)))
    if verbosity > 0:
        print("C2X2 LU " + str(coord) + "->" + str(state.vertexToSite(coord)) + " (-1,-1): " + str(C2x2_LU.size()))

    # ----- building C2x2_RU ----------------------------------------------------
    vec = (1, 0)
    shitf_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    if force_cpu:
        C = env.C[(shitf_coord, (1, -1))].cpu()
        T1 = env.T[(shitf_coord, (1, 0))].cpu()
        T2 = env.T[(shitf_coord, (0, -1))].cpu()
        a_1layer = state.site(shitf_coord).cpu()
    else:
        C = env.C[(shitf_coord, (1, -1))]
        T1 = env.T[(shitf_coord, (1, 0))]
        T2 = env.T[(shitf_coord, (0, -1))]
        a_1layer = state.site(shitf_coord)
    dimsA = a_1layer.size()

    A_reshaped= a_1layer.view( [3,3,3] + list(dimsA[1:]) )
    # double layer tensor with sites 1 and 3 contracted
    a = contiguous(einsum('mikefgh,mjkabcd->eafbgchdij', A_reshaped, conj(A_reshaped)))
    a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, 3, 3))

    # 0--C
    #	 1
    #	 0
    # 1--T1
    #	 2
    C2x2_RU = contract(C, T1, ([1], [0]))

    # 2<-0--T2--2 0--C
    #	 3<-1		 |
    #		   0<-1--T1
    #			  1<-2
    C2x2_RU = contract(C2x2_RU, T2, ([0], [2]))

    # 1<-2--T2------C
    #	    3	    |
    #	 45\0	    |
    # 2<-1--a--3 0--T1
    #	 3<-2	 0<-1
    C2x2_RU = contract(C2x2_RU, a, ([0, 3], [3, 0]))

    # permute 012334->120345
    # reshape (12)(03)45->0123
    # 0--C2x2
    # 23/|
    #	 1
    C2x2_RU = contiguous(permute(C2x2_RU, (1, 2, 0, 3, 4, 5)))
    C2x2_RU = view(C2x2_RU, (T2.size(0) * a.size(1), T1.size(2) * a.size(2), 3, 3))
    if verbosity > 0:
        print("C2X2 RU " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shitf_coord) + " (1,-1): " + str(
            C2x2_RU.size()))

    # ----- build upper part C2x2_LU--C2x2_RU -----------------------------------
    #
    # C2x2_LU--1 0--C2x2_RU
    # |				 |\23
    # 0				 1
    #
    # TODO is it worthy(performance-wise) to instead overwrite one of C2x2_LU,C2x2_RU ?
    upper_half = contract(C2x2_LU, C2x2_RU, ([1], [0]))

    # ----- building C2x2_RD ----------------------------------------------------
    vec = (1, 1)
    shitf_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    if force_cpu:
        C = env.C[(shitf_coord, (1, 1))].cpu()
        T1 = env.T[(shitf_coord, (0, 1))].cpu()
        T2 = env.T[(shitf_coord, (1, 0))].cpu()
        a_1layer = state.site(shitf_coord).cpu()
    else:
        C = env.C[(shitf_coord, (1, 1))]
        T1 = env.T[(shitf_coord, (0, 1))]
        T2 = env.T[(shitf_coord, (1, 0))]
        a_1layer = state.site(shitf_coord)
    dimsA = a_1layer.size()
    # reshape 27 = 3*3*3
    # A_reshaped = torch.zeros((3, 3, 3, dimsA[1], dimsA[2], dimsA[3], dimsA[4]), dtype=a_1layer.dtype,
    #                          device=a_1layer.device)
    # for s in range(27):
    #     n1, n2, n3 = fmap_inv(s)
    #     A_reshaped[n1, n2, n3, :, :, :, :] = a_1layer[s, :, :, :, :]
    # double layer tensor with sites 2 and 3 contracted
    a = contiguous(einsum('mikefgh,nikabcd->eafbgchdmn', A_reshaped, conj(A_reshaped)))
    a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, 3, 3))

    #	1<-0		0
    # 2<-1--T1--2 1--C
    C2x2_RD = contract(C, T1, ([1], [2]))

    #		  2<-0
    #	   3<-1--T2
    #			 2
    #	 0<-1	 0
    # 1<-2--T1---C
    C2x2_RD = contract(C2x2_RD, T2, ([0], [2]))

    #	 2<-0	 1<-2
    # 3<-1--a--3 3--T2
    #	    2\45	|
    #	    0	    |
    # 0<-1--T1------C
    C2x2_RD = contract(C2x2_RD, a, ([0, 3], [2, 3]))

    # permute 012345->120345
    # reshape (12)(03)45->0123
    C2x2_RD = contiguous(permute(C2x2_RD, (1, 2, 0, 3, 4, 5)))
    C2x2_RD = view(C2x2_RD, (T2.size(0) * a.size(0), T1.size(1) * a.size(1), 3, 3))

    #	 0
    #	 |/23
    # 1--C2x2
    if verbosity > 0:
        print("C2X2 RD " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shitf_coord) + " (1,1): " + str(
            C2x2_RD.size()))

    # ----- building C2x2_LD ----------------------------------------------------
    vec = (0, 1)
    shitf_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    if force_cpu:
        C = env.C[(shitf_coord, (-1, 1))].cpu()
        T1 = env.T[(shitf_coord, (-1, 0))].cpu()
        T2 = env.T[(shitf_coord, (0, 1))].cpu()
        a_1layer = state.site(shitf_coord).cpu()
    else:
        C = env.C[(shitf_coord, (-1, 1))]
        T1 = env.T[(shitf_coord, (-1, 0))]
        T2 = env.T[(shitf_coord, (0, 1))]
        a_1layer = state.site(shitf_coord)
    dimsA = a_1layer.size()
    # reshape 27 = 3*3*3
    # A_reshaped = torch.zeros((3, 3, 3, dimsA[1], dimsA[2], dimsA[3], dimsA[4]), dtype=a_1layer.dtype,
    #                          device=a_1layer.device)
    # for s in range(27):
    #     n1, n2, n3 = fmap_inv(s)
    #     A_reshaped[n1, n2, n3, :, :, :, :] = a_1layer[s, :, :, :, :]
    # double layer tensor with sites 1 and 2 contracted
    a = contiguous(einsum('mikefgh,milabcd->eafbgchdkl', A_reshaped, conj(A_reshaped)))
    a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2, 3, 3))

    # 0->1
    # T1--2
    # 1
    # 0
    # C--1->0
    C2x2_LD = contract(C, T1, ([0], [1]))

    # 1->0
    # T1--2->1
    # |
    # |	      0->2
    # C--0 1--T2--2->3
    C2x2_LD = contract(C2x2_LD, T2, ([0], [1]))

    # 0		   0->2
    # T1--1 1--a--3
    # |		   2\45
    # |		   2
    # C--------T2--3->1
    C2x2_LD = contract(C2x2_LD, a, ([1, 2], [1, 2]))

    # permute 012345->021345
    # reshape (02)(13)45->0123
    # 0
    # |/23
    # C2x2--1
    C2x2_LD = contiguous(permute(C2x2_LD, (0, 2, 1, 3, 4, 5)))
    C2x2_LD = view(C2x2_LD, (T1.size(0) * a.size(1), T2.size(1) * a.size(1), 3, 3))
    if verbosity > 0:
        print("C2X2 LD " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shitf_coord) + " (-1,1): " + str(
            C2x2_LD.size()))

    # ----- build lower part C2x2_LD--C2x2_RD -----------------------------------
    # 0			    0->3				 0			  3->1
    # |/23->12	    |/23->45   & permute |/12->23	  |/45
    # C2x2_LD--1 1--C2x2_RD			     C2x2_LD------C2x2_RD
    # TODO is it worthy(performance-wise) to instead overwrite one of C2x2_LD,C2x2_RD ?
    lower_half = contract(C2x2_LD, C2x2_RD, ([1], [1]))
    lower_half = permute(lower_half, (0, 3, 1, 2, 4, 5))

    # construct reduced density matrix by contracting lower and upper halfs
    # C2x2_LU------C2x2_RU
    # |				|\23->01
    # 0				1
    # 0				1
    # |/23			|/45
    # C2x2_LD------C2x2_RD
    rdm = contract(upper_half, lower_half, ([0, 1], [0, 1]))

    # permute into order of s1,s2,s3;s1',s2',s3' where primed indices
    # represent "ket"
    # 012345 -> 024135
    # C2x2_LU------C2x2_RU
    # |				|\03
    # 0				1
    # 0				1
    # |/14			|/25
    # C2x2_LD------C2x2_RD
    rdm = contiguous(permute(rdm, (0, 2, 4, 1, 3, 5)))

    rdm = _sym_pos_def_rdm(rdm, sym_pos_def=sym_pos_def, verbosity=verbosity, who=who)

    rdm = rdm.to(env.device)
    return rdm

def rdm2x2_dn_triangle_with_operator(coord, state, env, operator, force_cpu=False, verbosity=0):
    who = 'rdm2x2_dn_triangle'
    # ----- building C2x2_LU ----------------------------------------------------
    if force_cpu:
        C = env.C[(state.vertexToSite(coord), (-1, -1))].cpu()
        T1 = env.T[(state.vertexToSite(coord), (0, -1))].cpu()
        T2 = env.T[(state.vertexToSite(coord), (-1, 0))].cpu()
        a_1layer = state.site(coord).cpu()
        operator = operator.cpu()
    else:
        C = env.C[(state.vertexToSite(coord), (-1, -1))]
        T1 = env.T[(state.vertexToSite(coord), (0, -1))]
        T2 = env.T[(state.vertexToSite(coord), (-1, 0))]
        a_1layer = state.site(coord)
    dimsA = a_1layer.size()

    a = contiguous(einsum('mefgh,mabcd->eafbgchd', a_1layer, conj(a_1layer)))
    a = view(a, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2))
    a_op = contiguous(
        einsum('mefgh,mn,nabcd->eafbgchd', a_1layer, operator.view(27,27), conj(a_1layer)))
    a_op = view(a_op, (dimsA[1] ** 2, dimsA[2] ** 2, dimsA[3] ** 2, dimsA[4] ** 2))

    # C--10--T1--2
    # 0	  	 1
    C2x2_LU = contract(C, T1, ([1], [0]))

    # C------T1--2->1
    # 0	     1->0
    # 0
    # T2--2->3
    # 1->2
    C2x2_LU = contract(C2x2_LU, T2, ([0], [0]))

    # C-------T1--1->0
    # |	      0
    # |	      0
    # T2--3 1 a--3
    # 2->1	  2
    C2x2_LU_op = contract(C2x2_LU, a_op, ([0, 3], [0, 1]))
    C2x2_LU = contract(C2x2_LU, a, ([0, 3], [0, 1]))

    # permute 0123->1203
    # reshape (12)(03)->01
    # C2x2--1
    # |\23
    # 0

    C2x2_LU_op = contiguous(permute(C2x2_LU_op, (1, 2, 0, 3)))
    C2x2_LU_op = view(C2x2_LU_op, (T2.size(1) * a.size(2), T1.size(2) * a.size(3)))
    C2x2_LU = contiguous(permute(C2x2_LU, (1, 2, 0, 3)))
    C2x2_LU = view(C2x2_LU, (T2.size(1) * a.size(2), T1.size(2) * a.size(3)))
    if verbosity > 0:
        print("C2X2 LU " + str(coord) + "->" + str(state.vertexToSite(coord)) + " (-1,-1): " + str(C2x2_LU.size()))

    # ----- building C2x2_RU ----------------------------------------------------
    vec = (1, 0)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    if force_cpu:
        C = env.C[(shift_coord, (1, -1))].cpu()
        T1 = env.T[(shift_coord, (1, 0))].cpu()
        T2 = env.T[(shift_coord, (0, -1))].cpu()
        a_1layer = state.site(shift_coord).cpu()
    else:
        C = env.C[(shift_coord, (1, -1))]
        T1 = env.T[(shift_coord, (1, 0))]
        T2 = env.T[(shift_coord, (0, -1))]
        a_1layer = state.site(shift_coord)
    dimsA = a_1layer.size()
    

    # 0--C
    #	 1
    #	 0
    # 1--T1
    #	  2
    C2x2_RU = contract(C, T1, ([1], [0]))

    # 2<-0--T2--2 0--C
    #	 3<-1		 |
    #		   0<-1--T1
    #			  1<-2
    C2x2_RU = contract(C2x2_RU, T2, ([0], [2]))

    # 1<-2--T2------C
    #	    3	    |
    #	    0	    |
    # 2<-1--a--3 0--T1
    #	 3<-2  	 0<-1
    C2x2_RU = contract(C2x2_RU, a, ([0, 3], [3, 0]))

    # permute 0123->1203
    # reshape (12)(03)->01
    # 0--C2x2
    #    |
    #	 1
    C2x2_RU = contiguous(permute(C2x2_RU, (1, 2, 0, 3)))
    C2x2_RU = view(C2x2_RU, (T2.size(0) * a.size(1), T1.size(2) * a.size(2)))
    if verbosity > 0:
        print("C2X2 RU " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shitf_coord) + " (1,-1): " + str(
            C2x2_RU.size()))

    # ----- build upper part C2x2_LU--C2x2_RU -----------------------------------
    # C2x2_LU--1 0--C2x2_RU
    # |          	 |
    # 0			     1
    # TODO is it worthy(performance-wise) to instead overwrite one of C2x2_LU,C2x2_RU ?
    upper_half_op = contract(C2x2_LU_op, C2x2_RU, ([1], [0]))
    upper_half = contract(C2x2_LU, C2x2_RU, ([1], [0]))

    # ----- building C2x2_RD ----------------------------------------------------
    vec = (1, 1)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    if force_cpu:
        C = env.C[(shift_coord, (1, 1))].cpu()
        T1 = env.T[(shift_coord, (0, 1))].cpu()
        T2 = env.T[(shift_coord, (1, 0))].cpu()
        a_1layer = state.site(shift_coord).cpu()
    else:
        C = env.C[(shift_coord, (1, 1))]
        T1 = env.T[(shift_coord, (0, 1))]
        T2 = env.T[(shift_coord, (1, 0))]
        a_1layer = state.site(shift_coord)
    dimsA = a_1layer.size()

    #	1<-0		0
    # 2<-1--T1--2 1--C
    C2x2_RD = contract(C, T1, ([1], [2]))

    #		  2<-0
    #	   3<-1--T2
    #			 2
    #	 0<-1	 0
    # 1<-2--T1---C
    C2x2_RD = contract(C2x2_RD, T2, ([0], [2]))

    #	 2<-0	 1<-2
    # 3<-1--a--3 3--T2
    #	    2   	|
    #	    0	    |
    # 0<-1--T1------C
    C2x2_RD = contract(C2x2_RD, a, ([0, 3], [2, 3]))

    # permute 0123->1203
    # reshape (12)(03)->01
    C2x2_RD = contiguous(permute(C2x2_RD, (1, 2, 0, 3)))
    C2x2_RD = view(C2x2_RD, (T2.size(0) * a.size(0), T1.size(1) * a.size(1)))

    #	 0
    #	 |
    # 1--C2x2
    if verbosity > 0:
        print("C2X2 RD " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shitf_coord) + " (1,1): " + str(
            C2x2_RD.size()))

    # ----- building C2x2_LD ----------------------------------------------------
    vec = (0, 1)
    shift_coord = state.vertexToSite((coord[0] + vec[0], coord[1] + vec[1]))
    if force_cpu:
        C = env.C[(shift_coord, (-1, 1))].cpu()
        T1 = env.T[(shift_coord, (-1, 0))].cpu()
        T2 = env.T[(shift_coord, (0, 1))].cpu()
        a_1layer = state.site(shift_coord).cpu()
    else:
        C = env.C[(shift_coord, (-1, 1))]
        T1 = env.T[(shift_coord, (-1, 0))]
        T2 = env.T[(shift_coord, (0, 1))]
        a_1layer = state.site(shift_coord)
    dimsA = a_1layer.size()

    # 0->1
    # T1--2
    # 1
    # 0
    # C--1->0
    C2x2_LD = contract(C, T1, ([0], [1]))

    # 1->0
    # T1--2->1
    # |
    # |	   	  0->2
    # C--0 1--T2--2->3
    C2x2_LD = contract(C2x2_LD, T2, ([0], [1]))

    # 0		   0->2
    # T1--1 1--a--3
    # |		   2
    # |		   2
    # C--------T2--3->1
    C2x2_LD = contract(C2x2_LD, a, ([1, 2], [1, 2]))

    # permute 0123->0213
    # reshape (02)(13)->01
    # 0
    # |
    # C2x2--1
    C2x2_LD = contiguous(permute(C2x2_LD, (0, 2, 1, 3)))
    C2x2_LD = view(C2x2_LD, (T1.size(0) * a.size(0), T2.size(2) * a.size(3)))
    if verbosity > 0:
        print("C2X2 LD " + str((coord[0] + vec[0], coord[1] + vec[1])) + "->" + str(shitf_coord) + " (-1,1): " + str(
            C2x2_LD.size()))

    # ----- build lower part C2x2_LD--C2x2_RD -----------------------------------
    # 0			    0->1
    # |   	        |
    # C2x2_LD--1 1--C2x2_RD
    # TODO is it worthy(performance-wise) to instead overwrite one of C2x2_LD,C2x2_RD ?
    lower_half = contract(C2x2_LD, C2x2_RD, ([1], [1]))

    # construct reduced density matrix by contracting lower and upper halfs
    # C2x2_LU------C2x2_RU
    # |            |
    # 0			   1
    # 0			   1
    # |            |
    # C2x2_LD------C2x2_RD
    rdm_op = contract(upper_half_op, lower_half, ([0, 1], [0, 1]))
    rdm_id = contract(upper_half, lower_half, ([0, 1], [0, 1]))

    rdm = rdm_op/rdm_id
    rdm = rdm.to(env.device)
    return rdm