""" es_runners
"""

import numpy
import automol
import elstruct
import autofile
from routines.es._routines import _util as util
from routines.es import runner as es_runner
from lib import filesys
from lib.phydat import phycon


def tau_sampling(spc_info,
                 mod_thy_info, mod_ini_thy_info, ini_thy_save_fs,
                 tau_run_fs, tau_save_fs,
                 script_str, overwrite, nsamp_par, **opt_kwargs):
    """ Sample over torsions optimizing all other coordinates
    """

    # Read the geometry from the initial filesystem and set sampling
    geo = ini_thy_save_fs[-1].file.geometry.read(mod_ini_thy_info[1:4])
    zma = automol.geom.zmatrix(geo)
    tors_names = automol.geom.zmatrix_torsion_coordinate_names(geo)
    tors_ranges = automol.zmatrix.torsional_sampling_ranges(
        zma, tors_names)
    tors_range_dct = dict(zip(tors_names, tors_ranges))
    ich = spc_info[0]
    gra = automol.inchi.graph(ich)
    ntaudof = len(automol.graph.rotational_bond_keys(gra, with_h_rotors=False))
    nsamp = util.nsamp_init(nsamp_par, ntaudof)

    # Run through tau sampling process
    save_tau(
        tau_run_fs=tau_run_fs,
        tau_save_fs=tau_save_fs,
    )

    run_tau(
        zma=zma,
        spc_info=spc_info,
        thy_info=mod_thy_info,
        nsamp=nsamp,
        tors_range_dct=tors_range_dct,
        tau_run_fs=tau_run_fs,
        tau_save_fs=tau_save_fs,
        script_str=script_str,
        overwrite=overwrite,
        **opt_kwargs,
    )

    save_tau(
        tau_run_fs=tau_run_fs,
        tau_save_fs=tau_save_fs,
    )


def run_tau(
        zma, spc_info, thy_info, nsamp, tors_range_dct,
        tau_run_fs, tau_save_fs, script_str, overwrite, **kwargs):
    """ run sampling algorithm to find tau dependent geometries
    """
    if not tors_range_dct:
        print("No torsional coordinates. Setting nsamp to 1.")
        nsamp = 1

    tau_save_fs[0].create()

    vma = automol.zmatrix.var_(zma)
    if tau_save_fs[0].file.vmatrix.exists():
        existing_vma = tau_save_fs[0].file.vmatrix.read()
        assert vma == existing_vma
    tau_save_fs[0].file.vmatrix.write(vma)
    idx = 0
    nsamp0 = nsamp
    inf_obj = autofile.schema.info_objects.tau_trunk(0, tors_range_dct)
    while True:
        if tau_save_fs[0].file.info.exists():
            inf_obj_s = tau_save_fs[0].file.info.read()
            nsampd = inf_obj_s.nsamp
        elif tau_save_fs[0].file.info.exists():
            inf_obj_r = tau_save_fs[0].file.info.read()
            nsampd = inf_obj_r.nsamp
        else:
            nsampd = 0

        nsamp = nsamp0 - nsampd
        if nsamp <= 0:
            print('Reached requested number of samples. '
                  'Tau sampling complete.')
            break

        print("    New nsamp is {:d}.".format(nsamp))

        samp_zma, = automol.zmatrix.samples(zma, 1, tors_range_dct)
        tid = autofile.schema.generate_new_tau_id()
        locs = [tid]

        tau_run_fs[-1].create(locs)
        tau_run_prefix = tau_run_fs[-1].path(locs)
        run_fs = autofile.fs.run(tau_run_prefix)

        idx += 1
        print("Run {}/{}".format(idx, nsamp0))
        es_runner.run_job(
            job=elstruct.Job.OPTIMIZATION,
            script_str=script_str,
            run_fs=run_fs,
            geom=samp_zma,
            spc_info=spc_info,
            thy_level=thy_info,
            overwrite=overwrite,
            frozen_coordinates=tors_range_dct.keys(),
            **kwargs
        )

        nsampd += 1
        inf_obj.nsamp = nsampd
        tau_save_fs[0].file.info.write(inf_obj)
        tau_run_fs[0].file.info.write(inf_obj)


def save_tau(tau_run_fs, tau_save_fs):
    """ save the tau dependent geometries that have been found so far
    """

    saved_geos = [tau_save_fs[-1].file.geometry.read(locs)
                  for locs in tau_save_fs[-1].existing()]

    if not tau_run_fs[0].exists():
        print("No tau geometries to save. Skipping...")
    else:
        for locs in tau_run_fs[-1].existing():
            run_path = tau_run_fs[-1].path(locs)
            run_fs = autofile.fs.run(run_path)

            print("Reading from tau run at {}".format(run_path))

            ret = es_runner.read_job(
                job=elstruct.Job.OPTIMIZATION, run_fs=run_fs)
            if ret:
                inf_obj, inp_str, out_str = ret
                prog = inf_obj.prog
                method = inf_obj.method
                ene = elstruct.reader.energy(prog, method, out_str)

                geo = elstruct.reader.opt_geometry(prog, out_str)

                save_path = tau_save_fs[-1].path(locs)
                print(" - Saving...")
                print(" - Save path: {}".format(save_path))

                tau_save_fs[-1].create(locs)
                tau_save_fs[-1].file.geometry_info.write(inf_obj, locs)
                tau_save_fs[-1].file.geometry_input.write(inp_str, locs)
                tau_save_fs[-1].file.energy.write(ene, locs)
                tau_save_fs[-1].file.geometry.write(geo, locs)

                saved_geos.append(geo)

        # update the tau trajectory file
        filesys.mincnf.traj_sort(tau_save_fs)


def assess_pf_convergence(save_prefix, temps=(300., 500., 750., 1000., 1500.)):
    """ Determine how much the partition function has converged
    """
    # Get the energy of the mininimum-energy conformer
    cnf_save_fs = autofile.fs.conformer(save_prefix)
    min_cnf_locs = filesys.mincnf.min_energy_conformer_locators(cnf_save_fs)
    if min_cnf_locs:
        ene_ref = cnf_save_fs[-1].file.energy.read(min_cnf_locs)

    # Calculate sigma values at various temperatures for the PF
    tau_save_fs = autofile.fs.tau(save_prefix)
    for temp in temps:
        sumq = 0.
        sum2 = 0.
        idx = 0
        print('integral convergence for T = ', temp)
        for locs in tau_save_fs[-1].existing():
            idx += 1
            ene = tau_save_fs[-1].file.energy.read(locs)
            ene = (ene - ene_ref) * phycon.EH2KCAL
            tmp = numpy.exp(-ene*349.7/(0.695*temp))
            sumq = sumq + tmp
            sum2 = sum2 + tmp**2
            sigma = numpy.sqrt(
                (abs(sum2/float(idx)-(sumq/float(idx))**2))/float(idx))
            print(sumq/float(idx), sigma, 100.*sigma*float(idx)/sumq, idx)