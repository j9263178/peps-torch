import os
import context
import argparse
import yastn.yastn as yastn
import config as cfg
from ipeps.ipeps_abelian_c4v import *
from models.abelian import j1j2
from ctm.one_site_c4v_abelian.env_c4v_abelian import *
from ctm.one_site_c4v_abelian import ctmrg_c4v
from ctm.one_site_c4v_abelian.rdm_c4v import rdm2x1
from ctm.one_site_c4v_abelian import transferops_c4v
import json
import unittest
import logging
log = logging.getLogger(__name__)

# parse command line args and build necessary configuration objects
parser= cfg.get_args_parser()
# additional model-dependent arguments
parser.add_argument("--j1", type=float, default=1., help="nearest-neighbour coupling")
parser.add_argument("--j2", type=float, default=0., help="next nearest-neighbour coupling")
# additional observables-related arguments
parser.add_argument("--corrf_r", type=int, default=1, help="maximal correlation function distance")
parser.add_argument("--top_freq", type=int, default=-1, help="freuqency of transfer operator spectrum evaluation")
parser.add_argument("--top_n", type=int, default=2, help="number of leading eigenvalues"+
    "of transfer operator to compute")
parser.add_argument("--obs_freq", type=int, default=-1, help="frequency of computing observables"
    + " during CTM convergence")
parser.add_argument("--force_cpu", action='store_true', help="evaluate energy on cpu")
parser.add_argument("--yast_backend", type=str, default='np', 
    help="YAST backend", choices=['np','torch','torch_cpp'])
args, unknown_args = parser.parse_known_args()

def main():
    cfg.configure(args)
    cfg.print_config()
    from yastn.yastn.sym import sym_U1
    if args.yast_backend=='np':
        from yastn.yastn.backend import backend_np as backend
    elif args.yast_backend=='torch':
        from yastn.yastn.backend import backend_torch as backend
    elif args.yast_backend=='torch_cpp':
        from yastn.yastn.backend import backend_torch_cpp as backend
    settings_full= yastn.make_config(backend=backend, \
        default_device= cfg.global_args.device, default_dtype=cfg.global_args.dtype)
    settings= yastn.make_config(backend=backend, sym=sym_U1, \
        default_device= cfg.global_args.device, default_dtype=cfg.global_args.dtype)
    settings.backend.set_num_threads(args.omp_cores)
    settings.backend.random_seed(args.seed)

    model= j1j2.J1J2_C4V_BIPARTITE_NOSYM(settings_full, j1=args.j1, j2=args.j2)
    energy_f= model.energy_1x1_lowmem

    # initialize the ipeps
    if args.instate!=None:
        state= read_ipeps_c4v(args.instate, settings, default_irrep="A1")
        # state= state.add_noise(args.instate_noise)
    elif args.opt_resume is not None:
        state= IPEPS_ABELIAN_C4V(settings, None)
        state.load_checkpoint(args.opt_resume)
    else:
        raise ValueError("Missing trial state: --instate=None and --ipeps_init_type= "\
            +str(args.ipeps_init_type)+" is not supported")

    print(state)

    # 2) convergence criterion based on 2-site reduced density matrix 
    #    of nearest-neighbours
    def ctmrg_conv_f(state, env, history, ctm_args=cfg.ctm_args):
        if not history:
            history=dict({"log": []})
        rho2x1= rdm2x1(state, env, force_cpu=ctm_args.conv_check_cpu)
        dist= float('inf')
        if len(history["log"]) > 1:
            dist= (rho2x1-history["rdm"]).norm().item()

        # log dist and observables
        if args.obs_freq>0 and \
            (len(history["log"])%args.obs_freq==0 or 
            (len(history["log"])-1)%args.obs_freq==0):
            e_curr = energy_f(state, env, force_cpu=args.force_cpu)
            obs_values, obs_labels = model.eval_obs(state, env, force_cpu=args.force_cpu)
            print(", ".join([f"{len(history['log'])}",f"{dist}",f"{e_curr}"]\
                +[f"{v}" for v in obs_values]))
        else:
            print(f"{len(history['log'])}, {dist}")

        # update history and check convergence
        history["rdm"]=rho2x1
        history["log"].append(dist)
        if dist<ctm_args.ctm_conv_tol or len(history['log']) >= ctm_args.ctm_max_iter:
            log.info({"history_length": len(history['log']), "history": history['log'],
                "final_multiplets": env.compute_multiplets()[1]})
        converged= dist<ctm_args.ctm_conv_tol and \
            not len(history['log']) >= ctm_args.ctm_max_iter
        return converged, history

    # 3) initialize environment
    ctm_env= ENV_C4V_ABELIAN(args.chi, state=state, init=True)
    print(ctm_env)
    
    # 4) (optional) compute observables as given by initial environment 
    e_curr0= energy_f(state,ctm_env,force_cpu=args.force_cpu)
    obs_values0, obs_labels = model.eval_obs(state,ctm_env,force_cpu=args.force_cpu)
    print(", ".join(["epoch","energy"]+obs_labels))
    print(", ".join([f"{-1}",f"{e_curr0}"]+[f"{v}" for v in obs_values0]))

    # 5) (main) execute CTM algorithm
    ctm_env, *ctm_log = ctmrg_c4v.run(state, ctm_env, conv_check=ctmrg_conv_f)
    
    # 6) compute final observables
    e_curr0 = energy_f(state,ctm_env,force_cpu=args.force_cpu)
    obs_values0, obs_labels = model.eval_obs(state,ctm_env,force_cpu=args.force_cpu)
    history, t_ctm, t_obs= ctm_log
    print("\n")
    print(", ".join(["epoch","energy"]+obs_labels))
    print("FINAL "+", ".join([f"{e_curr0}"]+[f"{v}" for v in obs_values0]))
    print(f"TIMINGS ctm: {t_ctm} conv_check: {t_obs}")

    # ----- additional observables ---------------------------------------------
    # corrSS= model.eval_corrf_SS(state, ctm_env, args.corrf_r)
    # print("\n\nSS r "+" ".join([label for label in corrSS.keys()]))
    # for i in range(args.corrf_r):
    #     print(f"{i} "+" ".join([f"{corrSS[label][i]}" for label in corrSS.keys()]))

    # environment diagnostics
    print("\n\nspectrum(C)")
    D,m= ctm_env.compute_multiplets()
    for i in range(len(D)):
        print(f"{i} {D[i]}")

    # transfer operator spectrum 1-site-width channel
    # print("\n\nspectrum(T)")
    # l= transferops_c4v.get_Top_spec_c4v(args.top_n, state, ctm_env)
    # for i in range(l.size()[0]):
    #     print(f"{i} {l[i,0]} {l[i,1]}")

    # entanglement spectrum
    # transferops_c4v.get_full_EH_spec_exactTM(state)


if __name__=='__main__':
    if len(unknown_args)>0:
        print("args not recognized: "+str(unknown_args))
        raise Exception("Unknown command line arguments")
    main()


class TestCtmrg_D4_u1_Neel(unittest.TestCase):
    tol= 1.0e-6
    DIR_PATH = os.path.dirname(os.path.realpath(__file__))
    OUT_PRFX = "RESULT_test_run_ctmrg_D4_u1_Neel"
    BACKENDS = ['np', 'torch']

    def setUp(self):
        args.instate=self.DIR_PATH+"/../../../test-input/abelian/c4v/BFGS100LS_U1B_D4-chi97-j20.0-run0-iU1BD4j20chi97n0_blocks_1site_state.json"
        args.j2=0.1
        args.chi=36
        args.out_prefix=self.OUT_PRFX

    def test_run_ctmrg_D4_u1_Neel(self):
        from io import StringIO
        from unittest.mock import patch 
        from cmath import isclose

        for b_id in self.BACKENDS:
            with self.subTest(b_id=b_id):
                args.yast_backend=b_id

                with patch('sys.stdout', new = StringIO()) as tmp_out: 
                    main()
                tmp_out.seek(0)

                # parse FINAL observables
                final_obs=None
                final_opt_line=None
                OPT_OBS= OPT_OBS_DONE= False
                l= tmp_out.readline()
                while l:
                    print(l,end="")
                    if OPT_OBS and not OPT_OBS_DONE and l.rstrip()=="": OPT_OBS_DONE= True
                    if OPT_OBS and not OPT_OBS_DONE and len(l.split(','))>2:
                        final_opt_line= l
                    if "epoch, energy," in l and not OPT_OBS_DONE: 
                        OPT_OBS= True
                    if "FINAL" in l:
                        final_obs= l.rstrip()
                        break
                    l= tmp_out.readline()
                assert final_obs
                assert final_opt_line

                # compare with the reference
                ref_data="""
                -0.6283009062904991, 0.3359288237708855, 0.3359288237708855, 0.0, 0.0, -0.33448352974913287
                """

                # compare final observables from final state against expected reference 
                # drop first token, corresponding to iteration step
                fobs_tokens= [complex(x) for x in final_obs[len("FINAL"):].split(",")]
                ref_tokens= [complex(x) for x in ref_data.split(",")]
                for val,ref_val in zip(fobs_tokens, ref_tokens):
                    assert isclose(val,ref_val, rel_tol=self.tol, abs_tol=self.tol)

    def tearDown(self):
        args.opt_resume=None
        args.instate=None
        for f in [self.OUT_PRFX+"_state.json",self.OUT_PRFX+"_checkpoint.p",self.OUT_PRFX+".log"]:
            if os.path.isfile(f): os.remove(f)