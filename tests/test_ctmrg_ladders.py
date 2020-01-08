import context
import torch
import argparse
import config as cfg
from ipeps import *
from ctm.generic.env import *
from ctm.generic import ctmrg
from ctm.generic import transferops
from models import coupledLadders

if __name__=='__main__':
    # parse command line args and build necessary configuration objects
    parser= cfg.get_args_parser()
    # additional model-dependent arguments
    parser.add_argument("-alpha", type=float, default=0., help="inter-ladder coupling")
    # additional observables-related arguments
    parser.add_argument("-corrf_r", type=int, default=1, help="maximal correlation function distance")
    parser.add_argument("-top_n", type=int, default=2, help="number of leading eigenvalues"+
        "of transfer operator to compute")
    args = parser.parse_args()
    cfg.configure(args)
    cfg.print_config()
    torch.set_num_threads(args.omp_cores)
    
    model = coupledLadders.COUPLEDLADDERS(alpha=args.alpha)
    
    # initialize an ipeps
    # 1) define lattice-tiling function, that maps arbitrary vertex of square lattice
    # coord into one of coordinates within unit-cell of iPEPS ansatz    

    if args.instate!=None:
        state = read_ipeps(args.instate)
        if args.bond_dim > max(state.get_aux_bond_dims()):
            # extend the auxiliary dimensions
            state = extend_bond_dim(state, args.bond_dim)
        add_random_noise(state, args.instate_noise)
    elif args.ipeps_init_type=='RANDOM':
        bond_dim = args.bond_dim
        
        A = torch.rand((model.phys_dim, bond_dim, bond_dim, bond_dim, bond_dim),\
            dtype=cfg.global_args.dtype,device=cfg.global_args.device)
        B = torch.rand((model.phys_dim, bond_dim, bond_dim, bond_dim, bond_dim),\
            dtype=cfg.global_args.dtype,device=cfg.global_args.device)
        C = torch.rand((model.phys_dim, bond_dim, bond_dim, bond_dim, bond_dim),\
            dtype=cfg.global_args.dtype,device=cfg.global_args.device)
        D = torch.rand((model.phys_dim, bond_dim, bond_dim, bond_dim, bond_dim),\
            dtype=cfg.global_args.dtype,device=cfg.global_args.device)

        sites = {(0,0): A, (1,0): B, (0,1): C, (1,1): D}

        for k in sites.keys():
            sites[k] = sites[k]/torch.max(torch.abs(sites[k]))
        state = IPEPS(sites, lX=2, lY=2)
    else:
        raise ValueError("Missing trial state: -instate=None and -ipeps_init_type= "\
            +str(args.ipeps_init_type)+" is not supported")

    print(state)

    def ctmrg_conv_energy(state, env, history, ctm_args=cfg.ctm_args):
        with torch.no_grad():
            e_curr = model.energy_2x1_1x2(state, env)
            obs_values, obs_labels = model.eval_obs(state, env)
            history.append([e_curr.item()]+obs_values)
            print(", ".join([f"{len(history)}",f"{e_curr}"]+[f"{v}" for v in obs_values]))

            if len(history) > 1 and abs(history[-1][0]-history[-2][0]) < ctm_args.ctm_conv_tol:
                return True
        return False

    ctm_env_init = ENV(args.chi, state)
    init_env(state, ctm_env_init)
    print(ctm_env_init)

    e_curr0 = model.energy_2x1_1x2(state, ctm_env_init)
    obs_values0, obs_labels = model.eval_obs(state, ctm_env_init)

    print(", ".join(["epoch","energy"]+obs_labels))
    print(", ".join([f"{-1}",f"{e_curr0}"]+[f"{v}" for v in obs_values0]))

    ctm_env_init, *ctm_log = ctmrg.run(state, ctm_env_init, conv_check=ctmrg_conv_energy)

    # ----- S(0).S(r) -----
    corrSS= model.eval_corrf_SS((0,0), (1,0), state, ctm_env_init, args.corrf_r)
    print("\n\nSS[(0,0),(1,0)] r "+" ".join([label for label in corrSS.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrSS[label][i]}" for label in corrSS.keys()]))

    corrSS= model.eval_corrf_SS((0,0), (0,1), state, ctm_env_init, args.corrf_r)
    print("\n\nSS[(0,0),(0,1)] r "+" ".join([label for label in corrSS.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrSS[label][i]}" for label in corrSS.keys()]))

    corrSS= model.eval_corrf_SS((1,1), (1,0), state, ctm_env_init, args.corrf_r)
    print("\n\nSS[(1,1),(1,0)] r "+" ".join([label for label in corrSS.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrSS[label][i]}" for label in corrSS.keys()]))

    corrSS= model.eval_corrf_SS((1,1), (0,1), state, ctm_env_init, args.corrf_r)
    print("\n\nSS[(1,1),(0,1)] r "+" ".join([label for label in corrSS.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrSS[label][i]}" for label in corrSS.keys()]))

    # ----- (S(0).S(x))(S(rx).S(rx+x)) -----
    corrDD= model.eval_corrf_DD_H((0,0), (1,0), state, ctm_env_init, args.corrf_r)
    print("\n\nDD[(0,0),(1,0)] r "+" ".join([label for label in corrDD.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrDD[label][i]}" for label in corrDD.keys()]))

    corrDD= model.eval_corrf_DD_H((0,0), (0,1), state, ctm_env_init, args.corrf_r)
    print("\n\nDD[(0,0),(0,1)] r "+" ".join([label for label in corrDD.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrDD[label][i]}" for label in corrDD.keys()]))

    corrDD= model.eval_corrf_DD_H((1,1), (1,0), state, ctm_env_init, args.corrf_r)
    print("\n\nDD[(1,1),(1,0)] r "+" ".join([label for label in corrDD.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrDD[label][i]}" for label in corrDD.keys()]))

    corrDD= model.eval_corrf_DD_H((1,1), (0,1), state, ctm_env_init, args.corrf_r)
    print("\n\nDD[(1,1),(0,1)] r "+" ".join([label for label in corrDD.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrDD[label][i]}" for label in corrDD.keys()]))

    # ----- (S(0).S(y))(S(rx).S(rx+y)) -----
    corrDD_V= model.eval_corrf_DD_V((0,0),(1,0),state, ctm_env_init, args.corrf_r)
    print("\n\nDD_V[(0,0),(1,0)] r "+" ".join([label for label in corrDD_V.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrDD_V[label][i]}" for label in corrDD_V.keys()]))

    corrDD_V= model.eval_corrf_DD_V((0,0),(0,1),state, ctm_env_init, args.corrf_r)
    print("\n\nDD_V[(0,0),(0,1)] r "+" ".join([label for label in corrDD_V.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrDD_V[label][i]}" for label in corrDD_V.keys()]))

    corrDD_V= model.eval_corrf_DD_V((1,1),(1,0),state, ctm_env_init, args.corrf_r)
    print("\n\nDD_V[(1,1),(1,0)] r "+" ".join([label for label in corrDD_V.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrDD_V[label][i]}" for label in corrDD_V.keys()]))

    corrDD_V= model.eval_corrf_DD_V((1,1),(0,1),state, ctm_env_init, args.corrf_r)
    print("\n\nDD_V[(1,1),(0,1)] r "+" ".join([label for label in corrDD_V.keys()]))
    for i in range(args.corrf_r):
        print(f"{i} "+" ".join([f"{corrDD_V[label][i]}" for label in corrDD_V.keys()]))

    # environment diagnostics
    for c_loc,c_ten in ctm_env_init.C.items(): 
        u,s,v= torch.svd(c_ten, compute_uv=False)
        print(f"\n\nspectrum C[{c_loc}]")
        for i in range(args.chi):
            print(f"{i} {s[i]}")

    # transfer operator spectrum
    print("\n\nspectrum(T)[(0,0),(1,0)]")
    l= transferops.get_Top_spec(args.top_n, (0,0), (1,0), state, ctm_env_init)
    for i in range(l.size()[0]):
        print(f"{i} {l[i,0]} {l[i,1]}")

    print("\n\nspectrum(T)[(0,0),(0,1)]")
    l= transferops.get_Top_spec(args.top_n, (0,0), (0,1), state, ctm_env_init)
    for i in range(l.size()[0]):
        print(f"{i} {l[i,0]} {l[i,1]}")

    print("\n\nspectrum(T)[(1,1),(1,0)]")
    l= transferops.get_Top_spec(args.top_n, (1,1), (1,0), state, ctm_env_init)
    for i in range(l.size()[0]):
        print(f"{i} {l[i,0]} {l[i,1]}")

    print("\n\nspectrum(T)[(1,1),(0,1)]")
    l= transferops.get_Top_spec(args.top_n, (1,1), (0,1), state, ctm_env_init)
    for i in range(l.size()[0]):
        print(f"{i} {l[i,0]} {l[i,1]}")