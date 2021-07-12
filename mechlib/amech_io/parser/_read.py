""" Reads all of the MechDriver input files provided by the user
    at the start of the calculation.
"""

import os
import sys
import ioformat
import automol
from mechlib.amech_io import printer as ioprinter


# (name, path relative to job_path, required boolean)
INP_FILE = {
    'run': ('run.dat', 'inp/run.dat', True),
    'thy': ('theory.dat', 'inp/theory.dat', True),
    'mod': ('models.dat', 'inp/models.dat', True),
    'spc': ('species.csv', 'inp/species.csv', True),
    'mech': ('mechanism.dat', 'inp/mechanism.dat', False),
    'dat': ('species.dat', 'inp/species.dat', False)
}
# AUX_FILES:
#     'geo' : '.xyz files'


def read_amech_input(job_path):
    """ Reads all MechDriver input files provided by the user into strings.
        All whitespace and comment lines are stripped from the files.

        :param job_path: directory path where all input files exist
        :type job_path: str
        :rtype: dict[str:str]
    """

    # Read required input strings
    print('Reading input')
    run_str = ioformat.pathtools.read_file(
        job_path, INP_FILE['run'][1],
        remove_comments='#', remove_whitespace=True)

    thy_str = ioformat.pathtools.read_file(
        job_path, INP_FILE['thy'][1],
        remove_comments='#', remove_whitespace=True)

    mod_str = ioformat.pathtools.read_file(
        job_path, INP_FILE['mod'][1],
        remove_comments='#', remove_whitespace=True)

    spc_str = ioformat.pathtools.read_file(
        job_path, INP_FILE['spc'][1])

    mech_str = ioformat.pathtools.read_file(
        job_path, INP_FILE['mech'][1],
        remove_comments='!', remove_whitespace=True)

    # Read auxiliary input strings
    dat_str = ioformat.pathtools.read_file(
        job_path, INP_FILE['dat'][1],
        remove_comments='#')

    # Read structural and template files
    geo_dct, _ = _geometry_dictionary(job_path)
    act_dct, _ = _active_space_dictionary(job_path)

    # Place all of the input into a dictionary to pass on
    inp_str_dct = {
        'run': run_str,
        'thy': thy_str,
        'mod': mod_str,
        'spc': spc_str,
        'mech': mech_str,
        'dat': dat_str,
        'geo': geo_dct,
        'act': act_dct
    }

    # Assess if all required strings are present
    # _check_input_avail(inp_str_dct, gname_dct, aname_dct)

    return inp_str_dct


def _check_input_avail(inp_str_dct, gname_dct, aname_dct):
    """ Assess what input files have been supplied by the user
        by seeing which files consist of strings read from a file.
        Exit the program with error message if required input files
        are found to be missing.

        :param inp_str_dct: strings of input files read by code
        :type inp_str_dct: dict[str: str/dict[str:str]]
    """

    # Check all of the standard single-file inputs
    inp_missing = []
    for key, (name, _, req) in INP_FILE.items():
        if inp_str_dct[key] is not None:
            ioprinter.reading('{}...'.format(name), newline=1)
        else:
            print('Input file: ',
                  '{} file NOT found in inp directory.'.format(name))
            if req:
                inp_missing.append(name)

    if inp_missing:
        print('Missing Required inputs files, quitting job...')
        for name in inp_missing:
            print(name)
        sys.exit()

    # Check the auxiliary file dictionaries
    inf = (
        ('geo', '.xyz files for species', gname_dct),
        ('act', 'active space templates for TSs', aname_dct)
    )
    for key, msg, name_dct in inf:
        str_dct = inp_str_dct[key]
        if str_dct:
            print('Found {}:'.format(msg))
            for name in str_dct:
                print('{} - {}'.format(name, name_dct[name]))
        else:
            print('No active space template files for found.')


# formatters, dont know where to build this
def _geometry_dictionary(job_path):
    """ Reads any .xyz files provided by the user in the directory
        where other input files. The function extracts the mechanism name
        of the species/transition-state from the comment line of the .xyz file
        and indexes each .xyz string with this name.

        :param job_path: directory path where all input files exist
        :type job_path: str
        :rtype: dict[str: str]
    """

    geo_dct, path_dct = {}, {}
    _inp_paths = _inp_file_paths(job_path)
    if _inp_paths:
        for file_path, file_name in _inp_paths:
            if file_path.endswith('.xyz'):
                xyz_str = ioformat.pathtools.read_file(file_path, file_name)
                spc_name = automol.geom.comment_from_xyz_string(xyz_str)
                geo = automol.geom.from_xyz_string(xyz_str)
                if spc_name in geo_dct:
                    print('Warning: Dupilicate xyz geometry for ', spc_name)
                geo_dct[spc_name] = geo
                path_dct[spc_name] = file_name

    return geo_dct, path_dct


def _active_space_dictionary(job_path):
    """ Reads any active-space templates provided by the user in the directory
        where other input files. The function extracts the mechanism name
        from a top-line comment in the template file.

        Currently, only Molpro templates are supported.

        :param job_path: directory path where all input files exist
        :type job_path: str
        :rtype: tuple(str)
    """

    def _comment_name(aspace_str):
        """ read the species name from a comment line in template
            comment line in FIRST line of template of form
            ! {spc_name}
        """
        comm_line = aspace_str.splitlines()[0]
        comm_line.replace('!', '').strip()
        return comm_line

    aspace_dct, path_dct = {}, {}
    _inp_paths = _inp_file_paths(job_path)
    if _inp_paths:
        for file_path, file_name in _inp_paths:
            if file_path.endswith('.asp'):
                aspace_str = ioformat.pathtools.read_file(file_path, file_name)
                spc_name = _comment_name(aspace_str)
                if spc_name in aspace_dct:
                    print('Warning: Dupilicate active space geometry for ',
                          spc_name)
                aspace_dct[spc_name] = aspace_str
                path_dct[spc_name] = file_name

    return aspace_dct, path_dct


def _inp_file_paths(job_path):
    """ Utility function to get paths to all of the auxiliary input files.

        :param job_path: directory path where all input files exist
        :type job_path: str
        :rtype: tuple(str)
    """

    file_paths = ()

    geom_path = os.path.join(job_path, 'data')
    for dir_path, _, file_names in os.walk(geom_path):
        for file_name in file_names:
            file_paths += ((dir_path, file_name),)

    return file_paths
