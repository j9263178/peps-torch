import torch
import groups.su2 as su2
import config as cfg
from ctm.generic.env import ENV
from ctm.generic import rdm
from ctm.generic import corrf
from math import sqrt, pi
import itertools

def _cast_to_real(t):
    return t.real if t.is_complex() else t

class J1J2J4():
    def __init__(self, phys_dim=2, j1=1.0, j2=0, j4=0, global_args=cfg.global_args):
        r"""
        :param phys_dim: dimension of physical spin irrep, i.e. 2 for spin S=1/2 
        :param j1: nearest-neighbour interaction
        :param j2: next nearest-neighbour interaction
        :param j4: plaquette interaction
        :param global_args: global configuration
        :type phys_dim: int
        :type j1: float
        :type j2: float
        :type j4: float
        :type global_args: GLOBALARGS

        Build Spin-S :math:`J_1-J_2-J_4` Hamiltonian

        .. math:: H = J_1\sum_{<i,j>} \mathbf{S}_i.\mathbf{S}_j + J_2\sum_{<<i,j>>} 
                  \mathbf{S}_i.\mathbf{S}_j 
                  + \sum_{\langle i,j,k,l \rangle}[ 
                    (\mathbf{S}_i.\mathbf{S}_j)(\mathbf{S}_k.\mathbf{S}_l) 
                  + (\mathbf{S}_i.\mathbf{S}_l)(\mathbf{S}_j.\mathbf{S}_k)
                  - (\mathbf{S}_i.\mathbf{S}_k)(\mathbf{S}_j.\mathbf{S}_l) ]

        on the triangular lattice. Where the first sum runs over the pairs of sites `i,j` 
        which are nearest-neighbours (denoted as `<.,.>`), and the second sum runs over 
        pairs of sites `i,j` which are next nearest-neighbours (denoted as `<<.,.>>`),
        and finally the last sums runs over unique plaquettes composed of 
        pairs of edge sharing triangles.
        """
        self.dtype=global_args.torch_dtype
        self.device=global_args.device
        self.phys_dim=phys_dim
        self.j1=j1
        self.j2=j2
        self.j4=j4
        
        self.SS, self.SSSS, self.h_p= self.get_h()
        self.obs_ops= self.get_obs_ops()

    def get_h(self):
        s2 = su2.SU2(self.phys_dim, dtype=self.dtype, device=self.device)
        id2= torch.eye(self.phys_dim**2,dtype=self.dtype,device=self.device)
        id2= id2.view([self.phys_dim]*4).contiguous()
        expr_kron = 'ij,ab->iajb'
        SS= torch.einsum(expr_kron,s2.SZ(),s2.SZ()) + 0.5*(torch.einsum(expr_kron,s2.SP(),s2.SM()) \
            + torch.einsum(expr_kron,s2.SM(),s2.SP()))
        SS= SS.contiguous()
        
        SSSS= torch.einsum('ijab,klcd->ijklabcd',SS,SS)
        #
        #    l
        #  / |
        # j__k
        # | /
        # i
        h_p= SSSS + SSSS.permute(0,3,1,2,4,7,5,6) + SSSS.permute(0,2,1,3,4,6,5,7)

        return SS, SSSS, h_p

    def get_obs_ops(self):
        obs_ops = dict()
        s2 = su2.SU2(self.phys_dim, dtype=self.dtype, device=self.device)
        obs_ops["sz"]= s2.SZ()
        obs_ops["sp"]= s2.SP()
        obs_ops["sm"]= s2.SM()
        return obs_ops

    def energy_1x3(self,state,env):
        r"""
        :param state: wavefunction
        :param env: CTM environment
        :type state: IPEPS
        :type env: ENV
        :return: energy per site
        :rtype: float

        We assume 1x3 iPEPS which tiles the lattice with a tri-partite pattern composed 
        of three tensors A, B, and C::
            
            A--B--C--A--B--C
            | /| /| /| /| /|
            C--A--B--C--A--B
            | /| /| /| /| /|
            B--C--A--B--C--A
            | /| /| /| /| /|
            A--B--C--A--B--C

        For example, the NN of site A are only sites B and C.
        The evaluation of all NN terms requires two NN-RDMs and one NNN-RDM per site::
        
            A            B C
            |             /
            C, A--B, and A B   

        For NNN terms, there are again 3 non-equivalent terms, which can be accounted for 
        by one NNN-RDM and two NNNN-RDMs::

                              C   A
                                 /
            A B  B  C _A      B / C
             \    _ -          /
            C A, A  B  C, and A   B

        TODO plaquette
        """
        energy_nn=0.
        energy_nnn=0.
        energy_p=0.
        for coord in state.sites.keys():
            tmp_rdm_1x2= rdm.rdm1x2(coord,state,env)
            energy_nn+= torch.einsum('ijab,abij',self.SS,tmp_rdm_1x2)
            tmp_rdm_2x1= rdm.rdm2x1(coord,state,env)
            energy_nn+= torch.einsum('ijab,abij',self.SS,tmp_rdm_2x1)
            tmp_rdm_2x2_NNN_1n1= rdm.rdm2x2_NNN_1n1(coord,state,env)
            energy_nn+= torch.einsum('ijab,abij',self.SS,tmp_rdm_2x2_NNN_1n1)
        if abs(self.j2)>0:
            for coord in state.sites.keys():
                tmp_rdm_2x2_NNN_11= rdm.rdm2x2_NNN_1n1(coord,state,env)
                energy_nnn+= torch.einsum('ijab,abij',self.SS,tmp_rdm_2x2_NNN_11)
                # TODO
        if abs(self.j4)>0:
            for coord in state.sites.keys():
                # TODO
                pass

        num_sites= len(state.sites)
        energy_per_site= self.j1*energy_nn/num_sites + self.j2*energy_nnn/num_sites \
            + self.j4*energy_p/num_sites
        energy_per_site= _cast_to_real(energy_per_site)

        return energy_per_site

    def eval_obs(self,state,env):
        r"""
        :param state: wavefunction
        :param env: CTM environment
        :type state: IPEPS
        :type env: ENV
        :return:  expectation values of observables, labels of observables
        :rtype: list[float], list[str]

        Computes the following observables in order

            1. average magnetization over the unit cell,
            2. magnetization for each site in the unit cell
            3. :math:`\langle S^z \rangle,\ \langle S^+ \rangle,\ \langle S^- \rangle` 
               for each site in the unit cell

        where the on-site magnetization is defined as
        
        .. math::
            m &= \sqrt{ \langle S^z \rangle^2+\langle S^x \rangle^2+\langle S^y \rangle^2 }
        """
        # TODO optimize/unify ?
        # expect "list" of (observable label, value) pairs ?
        obs= dict({"avg_m": 0.})
        with torch.no_grad():
            for coord,site in state.sites.items():
                rdm1x1 = rdm.rdm1x1(coord,state,env)
                for label,op in self.obs_ops.items():
                    obs[f"{label}{coord}"]= torch.trace(rdm1x1@op)
                obs[f"m{coord}"]= sqrt(abs(obs[f"sz{coord}"]**2 + obs[f"sp{coord}"]*obs[f"sm{coord}"]))
                obs["avg_m"] += obs[f"m{coord}"]
            obs["avg_m"]= obs["avg_m"]/len(state.sites.keys())

            for coord,site in state.sites.items():
                tmp_rdm_1x2= rdm.rdm1x2(coord,state,env)
                tmp_rdm_2x1= rdm.rdm2x1(coord,state,env)
                tmp_rdm_2x2_NNN_1n1= rdm.rdm2x2_NNN_1n1(coord,state,env)
                SS1x2= torch.einsum('ijab,abij',tmp_rdm_1x2,self.SS)
                SS2x1= torch.einsum('ijab,abij',tmp_rdm_2x1,self.SS)
                SS2x2_NNN_1n1= torch.einsum('ijab,abij',self.SS,tmp_rdm_2x2_NNN_1n1)
                obs[f"SS2x1{coord}"]= _cast_to_real(SS2x1)
                obs[f"SS1x2{coord}"]= _cast_to_real(SS1x2)
                obs[f"SS2x2_NNN_1n1{coord}"]= _cast_to_real(SS2x2_NNN_1n1)
        
        # prepare list with labels and values
        obs_labels=["avg_m"]+[f"m{coord}" for coord in state.sites.keys()]\
            +[f"{lc[1]}{lc[0]}" for lc in list(itertools.product(state.sites.keys(), self.obs_ops.keys()))]
        obs_labels += [f"SS2x1{coord}" for coord in state.sites.keys()]
        obs_labels += [f"SS1x2{coord}" for coord in state.sites.keys()]
        obs_labels += [f"SS2x2_NNN_1n1{coord}" for coord in state.sites.keys()]
        obs_values=[obs[label] for label in obs_labels]
        return obs_values, obs_labels


class J1J2J4_1SITE(J1J2J4):
    def __init__(self, phys_dim=2, j1=1.0, j2=0, j4=0, global_args=cfg.global_args):
        r"""
        :param phys_dim: dimension of physical spin irrep, i.e. 2 for spin S=1/2 
        :param j1: nearest-neighbour interaction
        :param j2: next nearest-neighbour interaction
        :param j4: plaquette interaction
        :param global_args: global configuration
        :type phys_dim: int
        :type j1: float
        :type j2: float
        :type j4: float
        :type global_args: GLOBALARGS
        
        See :class:`J1J2J4`.
        """
        super().__init__(phys_dim=phys_dim, j1=j1, j2=j2, j4=j4, 
            global_args=global_args)
        s2 = su2.SU2(self.phys_dim, dtype=self.dtype, device=self.device)
        self.R= torch.linalg.matrix_exp( (2*pi/3)*(s2.SP()-s2.SM()) )
        

    def energy_1x3(self,state,env):
        r"""
        :param state: wavefunction
        :param env: CTM environment
        :type state: IPEPS
        :type env: ENV
        :return: energy per site
        :rtype: float

        We assume 1x3 iPEPS which tiles the lattice with a tri-partite pattern composed 
        of three tensors A, B, and C::
            
            A--B--C--A--B--C
            | /| /| /| /| /|
            C--A--B--C--A--B
            | /| /| /| /| /|
            B--C--A--B--C--A
            | /| /| /| /| /|
            A--B--C--A--B--C

        The tensors B and C are obtained from tensor A by applying a unitary R on 
        the physical index as

        .. math: 

            B^s = R^{ss'}A^{s'}
            C^s = (R^2)^{ss'}A^{s'}

        where the unitary R is a rotation around spin y-axis by :math:`2\pi/3`, i.e.
        :math:`R = exp(-i\frac{2\pi}{3}\sigma^y)`.

        For example, the NN of site A are only sites B and C.
        The evaluation of all NN terms requires two NN-RDMs and one NNN-RDM per site::
        
            A            B C
            |             /
            C, A--B, and A B   

        For NNN terms, there are again 3 non-equivalent terms, which can be accounted for 
        by one NNN-RDM and two NNNN-RDMs::

                              C   A
                                 /
            A B  B  C _A      B / C
             \    _ -          /
            C A, A  B  C, and A   B

        TODO plaquette
        """
        energy_nn=0.
        energy_nnn=0.
        energy_p=0.
        for coord in state.sites.keys():
            #
            # A--B
            tmp_rdm_2x1= rdm.rdm2x1(coord,state,env)
            energy_nn+= torch.einsum('ijab,abij',
                torch.einsum('ixay,xj,yb->ijab',self.SS,self.R,self.R),
                tmp_rdm_2x1)
            
            #
            # A
            # |
            # C
            tmp_rdm_1x2= rdm.rdm1x2(coord,state,env)
            energy_nn+= torch.einsum('ijab,abij',
                torch.einsum('ixay,xj,yb->ijab',self.SS,self.R@self.R,self.R@self.R),
                tmp_rdm_1x2)
            #
            # B C
            #  /
            # A B
            tmp_rdm_2x2_NNN_1n1= rdm.rdm2x2_NNN_1n1(coord,state,env)
            energy_nn+= torch.einsum('ijab,abij',
                torch.einsum('ixay,xj,yb->ijab',self.SS,self.R@self.R,self.R@self.R),
                tmp_rdm_2x2_NNN_1n1)
        if abs(self.j2)>0:
            for coord in state.sites.keys():
                tmp_rdm_2x2_NNN_11= rdm.rdm2x2_NNN_1n1(coord,state,env)
                energy_nnn+= torch.einsum('ijab,abij',self.SS,tmp_rdm_2x2_NNN_11)
                # TODO
        if abs(self.j4)>0:
            for coord in state.sites.keys():
                # TODO
                pass

        num_sites= len(state.sites)
        energy_per_site= self.j1*energy_nn/num_sites + self.j2*energy_nnn/num_sites \
            + self.j4*energy_p/num_sites
        energy_per_site= _cast_to_real(energy_per_site)

        return energy_per_site

    def eval_obs(self,state,env):
        r"""
        :param state: wavefunction
        :param env: CTM environment
        :type state: IPEPS
        :type env: ENV
        :return:  expectation values of observables, labels of observables
        :rtype: list[float], list[str]

        Computes the following observables in order

            1. average magnetization over the unit cell,
            2. magnetization for each site in the unit cell
            3. :math:`\langle S^z \rangle,\ \langle S^+ \rangle,\ \langle S^- \rangle` 
               for each site in the unit cell

        where the on-site magnetization is defined as
        
        .. math::
            m &= \sqrt{ \langle S^z \rangle^2+\langle S^x \rangle^2+\langle S^y \rangle^2 }
        """
        # TODO optimize/unify ?
        # expect "list" of (observable label, value) pairs ?
        obs= dict({"avg_m": 0.})
        with torch.no_grad():
            # single-site
            for coord,site in state.sites.items():
                rdm1x1 = rdm.rdm1x1(coord,state,env)
                for label,op in self.obs_ops.items():
                    obs[f"{label}{coord}"]= torch.trace(rdm1x1@op)
                obs[f"m{coord}"]= sqrt(abs(obs[f"sz{coord}"]**2 + obs[f"sp{coord}"]*obs[f"sm{coord}"]))
                obs["avg_m"] += obs[f"m{coord}"]
            obs["avg_m"]= obs["avg_m"]/len(state.sites.keys())

            # two-site
            for coord,site in state.sites.items():
                # s0
                # s1
                tmp_rdm_1x2= rdm.rdm1x2(coord,state,env)
                # s0 s1
                tmp_rdm_2x1= rdm.rdm2x1(coord,state,env)
                tmp_rdm_2x2_NNN_1n1= rdm.rdm2x2_NNN_1n1(coord,state,env)
                #
                # A--B
                SS2x1= torch.einsum('ijab,abij',tmp_rdm_2x1,
                    torch.einsum('ixay,xj,yb->ijab',self.SS,self.R,self.R))
                obs[f"SS2x1AB{coord}"]= _cast_to_real(SS2x1)

                # #
                # # B--C
                # SS2x1= torch.einsum('ijab,abij',
                #     torch.einsum('mnxy,mi,xa,nj,yb->ijab',self.SS,self.R,self.R,
                #         self.R@self.R, self.R@self.R),
                #     tmp_rdm_2x1)
                # obs[f"SS2x1BC{coord}"]= _cast_to_real(SS2x1)

                # #
                # # C--A
                # SS2x1= torch.einsum('ijab,abij',
                #     torch.einsum('mjxb,mi,xa->ijab',self.SS,
                #         self.R@self.R, self.R@self.R),
                #     tmp_rdm_2x1)
                # obs[f"SS2x1CA{coord}"]= _cast_to_real(SS2x1)

                #
                # A
                # |
                # C
                SS1x2= torch.einsum('ijab,abij',tmp_rdm_1x2,
                    torch.einsum('ixay,xj,yb->ijab',self.SS,self.R@self.R,self.R@self.R))
                obs[f"SS1x2AC{coord}"]= _cast_to_real(SS1x2)

                # #
                # # C
                # # |
                # # B
                # SS1x2= torch.einsum('ijab,abij',tmp_rdm_1x2,
                #     torch.einsum('mnxy,mi,xa,nj,yb->ijab',self.SS,
                #         self.R@self.R, self.R@self.R,
                #         self.R,self.R))
                # obs[f"SS1x2CB{coord}"]= _cast_to_real(SS1x2)

                # #
                # # B
                # # |
                # # A
                # SS1x2= torch.einsum('ijab,abij',tmp_rdm_1x2,
                #     torch.einsum('mjxb,mi,xa->ijab',self.SS,
                #         self.R, self.R))
                # obs[f"SS1x2BA{coord}"]= _cast_to_real(SS1x2)



                #
                # B C
                #  /
                # A B
                SS2x2_NNN_1n1= torch.einsum('ijab,abij',tmp_rdm_2x2_NNN_1n1,
                    torch.einsum('ixay,xj,yb->ijab',self.SS,self.R@self.R,self.R@self.R))
                
                obs[f"SS2x2_NNN_1n1{coord}"]= _cast_to_real(SS2x2_NNN_1n1)
        
        # prepare list with labels and values
        obs_labels=["avg_m"]+[f"m{coord}" for coord in state.sites.keys()]\
            +[f"{lc[1]}{lc[0]}" for lc in list(itertools.product(state.sites.keys(), self.obs_ops.keys()))]
        obs_labels += [f"SS2x1AB{coord}" for coord in state.sites.keys()]
            # + [f"SS2x1BC{coord}" for coord in state.sites.keys()] \
            # + [f"SS2x1CA{coord}" for coord in state.sites.keys()]
        obs_labels += [f"SS1x2AC{coord}" for coord in state.sites.keys()]
            # + [f"SS1x2CB{coord}" for coord in state.sites.keys()] \
            # + [f"SS1x2BA{coord}" for coord in state.sites.keys()]
        obs_labels += [f"SS2x2_NNN_1n1{coord}" for coord in state.sites.keys()]
        obs_values=[obs[label] for label in obs_labels]
        return obs_values, obs_labels