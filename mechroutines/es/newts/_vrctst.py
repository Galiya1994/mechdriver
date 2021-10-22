""" Generate the information necessary to product the vrctst input files
"""

import automol
import autofile
from phydat import phycon
import varecof_io
import elstruct
import autorun
from mechanalyzer.inf import rxn as rinfo
from mechlib.amech_io import printer as ioprinter
from mechlib import filesys
from mechroutines.es.runner import scan
from mechroutines.es.runner import qchem_params
from mechroutines.es._routines import sp
from mechroutines.es.newts import _rpath as rpath


# CENTRAL FUNCTION TO WRITE THE VARECOF INPUT FILES AND RUN THE PROGRAM
def calc_vrctst_flux(ts_dct,
                     thy_inf_dct, thy_method_dct, mref_params,
                     es_keyword_dct,
                     runfs_dct, savefs_dct):
    """ Set up n VRC-TST calculations to get the flux file
    """

    # Build VRC-TST stuff
    vrc_fs = runfs_dct['vrctst']
    vrc_path = vrc_fs[-1].path((0,))
    vrc_dct = autorun.varecof.VRC_DCT  # need code to input one
    machine_dct = {}  # how to do this when on a node

    # Get a bunch of info that describes the grid
    scan_inf_dct = _scan_inf_dct(ts_dct, savefs_dct)

    # Calculate the correction potential along the MEP
    inf_sep_ene, npot, zma_for_inp = _build_correction_potential(
        ts_dct, scan_inf_dct,
        thy_inf_dct, thy_method_dct, mref_params,
        es_keyword_dct,
        runfs_dct, savefs_dct,
        vrc_dct, vrc_path)

    # Write the VaReCoF input files
    inp_strs = autorun.varecof.write_varecof_input(
        vrc_path,
        zma_for_inp, scan_inf_dct['rct_zmas'],
        npot, scan_inf_dct['rxn_frm_keys'],
        machine_dct, vrc_dct)

    rxn_info = ts_dct['rxn_info']
    ts_info = rinfo.ts_info(rxn_info)
    mod_var_sp1_thy_info = thy_inf_dct['mod_var_splvl1']
    cas_kwargs = mref_params['var_scnlvl']
    molp_tmpl_str = varecof_io.writer.molpro_template(
        ts_info, mod_var_sp1_thy_info, inf_sep_ene, cas_kwargs)

    inp_strs += (('', molp_tmpl_str),)

    # Run VaReCoF to generate flux file
    flux_str = autorun.varecof.flux_file(
        autorun.SCRIPT_DCT['varecof'], autorun.SCRIPT_DCT['mcflux'],
        vrc_path, inp_strs)

    # Save the flux file
    if flux_str is not None:
        filesys.save.flux(flux_str, inp_strs,
                          savefs_dct['vrctst'], vrc_locs=(0,))


def _scan_inf_dct(ts_dct, savefs_dct):
    """ Determine all informationa about the scans and guess information
    """

    # Build initial coord, grid, and other info
    zrxn, ts_zma = ts_dct['zrxn'], ts_dct['zma']
    scan_inf = automol.reac.build_scan_info(zrxn, ts_zma)
    coord_names, _, coord_grids, update_guess = scan_inf

    # Get fol constraint dct
    _rcts_cnf_fs = savefs_dct['rcts_cnf']
    rct_zmas = ()
    for (_, cnf_save_fs, min_locs, _) in _rcts_cnf_fs:
        zma_fs = autofile.fs.zmatrix(cnf_save_fs[-1].path(min_locs))
        rct_zmas += (zma_fs[-1].file.zmatrix.read((0,)),)
    constraint_dct = varecof_io.writer.intramolecular_constraint_dct(
        ts_zma, rct_zmas)

    # Get indices for potentials and input
    frm_bnd_key, = automol.graph.ts.forming_bond_keys(zrxn.forward_ts_graph)

    return {
        'coord_names': coord_names,
        'full_grid': sorted(list(coord_grids[0]) + list(coord_grids[1])),
        'grid_val_for_zma': coord_grids[0][-1],
        'inf_locs': (coord_names, (coord_grids[0][0],)),
        'update_guess': update_guess,
        'constraint_dct': constraint_dct,
        'rxn_bond_keys': (min(frm_bnd_key), max(frm_bnd_key))
    }


# FUNCTIONS TO SET UP THE libcorrpot.so FILE USED BY VARECOF
def _build_correction_potential(ts_dct, scan_inf_dct,
                                thy_inf_dct, thy_method_dct, mref_params,
                                es_keyword_dct,
                                runfs_dct, savefs_dct,
                                vrc_dct, vrc_path):
    """  use the MEP potentials to compile the correction potential .so file
    """
    rxn_info = ts_dct['rxn_info']
    ts_info = rinfo.ts_info(rxn_info)

    # Run the constrained and full opt potential scans
    _run_potentials(ts_info, scan_inf_dct,
                    thy_inf_dct, thy_method_dct, mref_params,
                    es_keyword_dct, runfs_dct, savefs_dct)

    # Obtain the energy at infinite separation
    inf_sep_ene = rpath.inf_sep_ene(
        ts_dct, thy_inf_dct, mref_params,
        savefs_dct, runfs_dct, es_keyword_dct)

    # Read the values for the correction potential from filesystem
    potentials, pot_labels, zma_for_inp = _read_potentials(
        scan_inf_dct, thy_inf_dct, savefs_dct)

    # Build correction potential .so file used by VaReCoF
    autorun.varecof.compile_potentials(
        vrc_path, scan_inf_dct['full_grid'], potentials,
        scan_inf_dct['rxn_bond_idxs'], vrc_dct['fortran_compiler'],
        dist_restrict_idxs=(),
        pot_labels=pot_labels,
        pot_file_names=[vrc_dct['spc_name']],
        spc_name=vrc_dct['spc_name'])

    # Set zma if needed
    if zma_for_inp is None:
        zma_for_inp = ts_dct['zma']

    return inf_sep_ene, len(potentials), zma_for_inp


def _run_potentials(ts_info, scan_inf_dct,
                    thy_inf_dct, thy_method_dct, mref_params,
                    es_keyword_dct, runfs_dct, savefs_dct):
    """ Run and save the scan along both grids while
          (1) optimization: constraining only reaction coordinate, then
          (2) optimization: constraining all intermolecular coordinates
          (3) single-point energy on scan (1)
    """

    # Get fs and method objects
    scn_run_fs = runfs_dct['vscnlvl_scn']
    scn_save_fs = savefs_dct['vscnlvl_scn']
    cscn_run_fs = runfs_dct['vscnlvl_cscn']
    cscn_save_fs = savefs_dct['vscnlvl_cscn']
    sp_thy_info = thy_inf_dct['mod_var_splvl1']

    opt_script_str, opt_kwargs = qchem_params(
        thy_method_dct['var_scnlvl'], elstruct.Job.OPTIMIZATION)
    cas_kwargs = mref_params['var_scnlvl']
    opt_kwargs.update(cas_kwargs)

    sp_script_str, sp_kwargs = qchem_params(
        thy_method_dct['var_splvl1'], elstruct.Job.OPTIMIZATION)
    sp_cas_kwargs = mref_params['var_splvl1']
    sp_kwargs.update(sp_cas_kwargs)

    # Run optimization scans
    for constraints in (None, scan_inf_dct['constraint_dct']):
        if constraints is None:
            _run_fs = scn_run_fs
            _save_fs = scn_save_fs
            ioprinter.info_message('Running full scans..', newline=1)
        else:
            _run_fs = cscn_run_fs
            _save_fs = cscn_save_fs
            ioprinter.info_message('Running constrained scans..', newline=1)

        scan.execute_scan(
            zma=scan_inf_dct['inf_sep_zma'],
            spc_info=ts_info,
            mod_thy_info=thy_inf_dct['mod_var_scnlvl'],
            coord_names=scan_inf_dct['coord_names'],
            coord_grids=scan_inf_dct['full_grid'],
            scn_run_fs=_run_fs,
            scn_save_fs=_save_fs,
            scn_typ='relaxed',
            script_str=opt_script_str,
            overwrite=es_keyword_dct['overwrite'],
            update_guess=scan_inf_dct['update_guess'],
            reverse_sweep=False,
            saddle=False,
            constraint_dct=constraints,
            retryfail=True,
            **cas_kwargs
        )

    # Run the single points on top of the initial, full scan
    if sp_thy_info is not None:
        for locs in scn_save_fs[-1].existing(scan_inf_dct['coord_names']):
            scn_run_fs[-1].create(locs)
            geo = scn_save_fs[-1].file.geometry.read(locs)
            zma = scn_save_fs[-1].file.zmatrix.read(locs)
            sp.run_energy(zma, geo, ts_info, sp_thy_info,
                          scn_run_fs, scn_save_fs, locs,
                          sp_script_str, es_keyword_dct['overwrite'],
                          highspin=False, **sp_kwargs)


def _read_potentials(scan_inf_dct, thy_inf_dct, savefs_dct):
    """ Read values form the filesystem to get the values to
        correct ht MEP
    # Read the energies from the full and constrained opts along MEP
    """

    scn_save_fs = savefs_dct['vscnlvl_scn']
    cscn_save_fs = savefs_dct['vscnlvl_cscn']
    mod_var_scn_thy_info = thy_inf_dct['mod_var_scnlvl']
    mod_var_sp1_thy_info = thy_inf_dct['mod_var_splvl1']
    coord_name = scan_inf_dct['coord_names'][0]
    full_grid = scan_inf_dct['full_grid']
    constraint_dct = scan_inf_dct['constraint_dct']
    grid_val_for_zma = scan_inf_dct['grid_val_for_zma']

    # build objects for loops
    smp_pot, const_pot, sp_pot = [], [], []
    scans = (
        (scn_save_fs, mod_var_scn_thy_info[1:4]),
        (cscn_save_fs, mod_var_scn_thy_info[1:4])
    )
    if mod_var_sp1_thy_info is not None:
        scans += ((scn_save_fs, mod_var_sp1_thy_info[1:4]),)

    for idx, (scn_fs, thy_info) in enumerate(scans):
        for grid_val in full_grid:
            if idx in (0, 2):
                locs = [[coord_name], [grid_val]]
            else:
                locs = [constraint_dct, [coord_name], [grid_val]]
            sp_ene = filesys.read.energy(scn_fs, locs, thy_info)

            # Store the energy in a lst
            if idx == 0:
                smp_pot.append(sp_ene)
            elif idx == 1:
                const_pot.append(sp_ene)
            elif idx == 2:
                sp_pot.append(sp_ene)

    # Calculate each of the correction potentials
    relax_corr_pot, sp_corr_pot, full_corr_pot = [], [], []
    for i, _ in enumerate(smp_pot):
        relax_corr = (smp_pot[i] - const_pot[i]) * phycon.EH2KCAL
        relax_corr_pot.append(relax_corr)
        if sp_pot:
            sp_corr = (sp_pot[i] - smp_pot[i]) * phycon.EH2KCAL
            sp_corr_pot.append(sp_corr)
        else:
            sp_corr = 0.0
        full_corr_pot.append(relax_corr + sp_corr)

    # Collate the potentials together in a list
    if sp_pot:
        potentials = [relax_corr_pot, sp_corr_pot, full_corr_pot]
        potential_labels = ['relax', 'sp', 'full']
    else:
        potentials = [relax_corr_pot, full_corr_pot]
        potential_labels = ['relax', 'full']

    # Get zma used to make structure.inp and divsur.inp
    inp_zma_locs = [[coord_name], [grid_val_for_zma]]
    if scn_save_fs[-1].file.zmatrix.exists(inp_zma_locs):
        zma_for_inp = scn_save_fs[-1].file.zmatrix.read(inp_zma_locs)
    else:
        zma_for_inp = None

    return potentials, potential_labels, zma_for_inp