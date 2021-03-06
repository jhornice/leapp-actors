# coding: utf-8


import json
import os
import sys
import subprocess
import xml.etree.ElementTree as ET
import fnmatch
from collections import namedtuple, OrderedDict

import augeas


""" Check _actor.yaml for detailed description of inputs and examples.


Note: We include additional non-default lenses from the "lenses" directory
      using the --include directive.
      These lenses may override the default augeas lenses.


TODO:

- Add support for multiple configuration instances. Instead of specifying
  lens, directives, load_files and prefix_for_relative only for one specific
  lens (e.g. Httpd), make sure that uper can pass to this actor list of such
  configuration instances (e.g. Httpd, SQL, Wordpress). Then, let this actor
  compute necessary transformations for all of them, instead of only for one.


Possible Improvements:

- Providing prefix for directive argument paths could be automatized by running
  vanilla augeas actor before resolving directives. Then put output of augeas
  actor to relevant scanner and extract root directory.
  After that, run this module.
"""


INPUT = json.load(sys.stdin)

# aug_input (recursive directive processing capability) is optional
AUG_INPUT = INPUT.get("aug_input", None)
if AUG_INPUT:
    AUG_INPUT = AUG_INPUT[0]

AUG = augeas.Augeas()
Lens = namedtuple("Lens", "name included excluded")


def augeas_get_known_files():
    return AUG.match("/augeas/files//path[../lens = \"@{LENS}\" or ../lens = \"{LENS}.lns\"]".format(
        LENS=AUG_INPUT["lens"]))


def augeas_add_file_to_filter(file_path):
    AUG.set("/augeas/load/{LENS}/incl[last()+1]".format(LENS=AUG_INPUT["lens"]), file_path)
    AUG.load()


def augeas_get_directive_argument(augeas_file_path, directive_name):
    return AUG.match("{AUGEAS_FILE_PATH}/directive[. = \"{DIRECTIVE}\"]/arg".format(
        AUGEAS_FILE_PATH=augeas_file_path, DIRECTIVE=directive_name))


def augeas_get_path_value(augeas_path):
    return AUG.get(augeas_path)


def get_transformations():
    """ Finds config files based on provided lens and directives and returns
    relevant augeas transformations for augtool.

    We can do augeas lens transformation. e.g.:

        Http.lns incl /opt/config.conf

    which basically means: add "include /opt/config.conf" to Httpd lens filter.
    This can be later used as:

    > augtool -At "Http.lns incl /opt/config.conf"

    So, we find all possible config files based on provided directives and output
    transformations for all of them.

    We return list of transformations that can be glued to the augtool command.
    """

    def gather_files_for_resolution():
        """ What We do here is basically the same thing as running:

        > augtool match /augeas/files//path[../lens = "@Httpd" or ../lens = "Httpd.lns"]

        Which produces list of files that Httpd lens loaded into augeas internal
        structures.
        After that, We put unresolved file paths into the set, so they will get
        eventually resolved.
        """
        for augeas_file_path in augeas_get_known_files():
            path_value = augeas_get_path_value(augeas_file_path)
            if path_value not in files_already_resolved:
                files_to_resolve.add(path_value)

    # User did not supply input
    # (probably do not want to use recirsive directive processing capability)
    if AUG_INPUT is None:
        return []

    # We need at minimum lens name"
    if "lens" not in AUG_INPUT:
        return []

    # Augeas is case sensitive and people will forget.
    AUG_INPUT["lens"] = AUG_INPUT["lens"].capitalize()

    # Basically the same thing as adding file path directly to the lens filter.
    if "load_files" in AUG_INPUT:
        for file_path in AUG_INPUT["load_files"]:
            augeas_add_file_to_filter(file_path)

    files_to_resolve = set()
    files_already_resolved = set()
    gather_files_for_resolution()

    while len(files_to_resolve) > 0 and "directives" in AUG_INPUT:
        augeas_file_path = files_to_resolve.pop()
        files_already_resolved.add(augeas_file_path)

        for directive in AUG_INPUT["directives"]:
            # Locate all directive keywords in config and get its location in augeas tree.
            directive_arg_paths = augeas_get_directive_argument(augeas_file_path, directive)

            # Get values for all located directives.
            for path in directive_arg_paths:
                directive_arg = augeas_get_path_value(path)

                # If there is relative path in config file, We need to prepend
                # supplied prefix.
                if not directive_arg.startswith('/') and "prefix_for_relative" in AUG_INPUT:
                    directive_arg = AUG_INPUT["prefix_for_relative"] + '/' + directive_arg

                augeas_add_file_to_filter(directive_arg)
                gather_files_for_resolution()

    transformations = []
    for augeas_file_path in augeas_get_known_files():
        # Need to extract filesystem path from augeas tree.
        fs_file_path = augeas_file_path.split("/augeas/files")[1].split("/path")[0]
        lens_and_file = "{LENS}.lns incl {FS_FILE_PATH}".format(LENS=AUG_INPUT["lens"], FS_FILE_PATH=fs_file_path)
        transformations.append("-t" + lens_and_file)

    return transformations


def get_lenses():
    ''' Load information about lenses from Augease's `/augeas` sub-key

        This information represents:
        1) Lens name
        2) Included paths
           We use these to find which path corresponds to whichh lens
        3) Excluded paths
           Currently unused
    '''
    aug = subprocess.check_output(["augtool"] + get_transformations() + ["--include=lenses", "dump-xml", "/augeas"])

    root = ET.fromstring(aug)
    loaded = []

    # Auges/Node stores information about all supported lenses
    for ld in root.findall("./node[@label='augeas']/node[@label='load']/*"):
        name, incl, excl = ld.attrib['label'], [], []

        for ie in ld.findall("./node[@label='incl']/value"):
            incl.append(ie.text)
        for ee in ld.findall("./node[@label='excl']/value"):
            excl.append(ie.text)

        loaded.append(Lens(name, incl, excl))

    return loaded


def find_lens(path, lens_data):
    ''' Find lens for given `path` in `lens_data` '''
    #TODO: Optimize - this function is very slow
    for ld in lens_data:
        for ip in ld.included:
            if fnmatch.fnmatch(path, ip):
                return ld

    return None


def process_augeas_data(lens_data):
    ''' The function starts by iterating first level of data nodes, but
        it's important to first understand how Augeas representes information
        about files.

        Augeas Output
        -------------

         The output XML is nested collection of key-values based on file system
         structure:

             /etc/httpd.conf/DocumentRoot/Value
             /etc/httpd.conf/IfModule/mime_type/
             ...

         So until a certain depth, the chain of key-values represents a file path,
         and the remaining key-values represent parsed elements from the file.

        So based on this layout, the general algorithm is as follows:

        1) Iterate first level of nodes
        2) Check if the current node represents a real file-system path to a file
         2.a) File Path
              Children nodes of the current node are recursively converted to dictionaries
              holding nodes properties
         2.a.a) Lens for the pathh is found, in worst case scenario we need to iterate over
                all lenses (N) and all their `inclusive` paths (M), so this operation is very expensive
                since we need to call `fnmatch` N×M times per each file path
         2.a.b) Property elements are recursively descended using `_rec_props()` function
                which returns the whole property tree
         2.b) Non-File Path
              Intermediary component of the path i.e. directory - recurse using `_rec` until we hit 2.a)
        3) All data are accumulated in `data` variable
    '''
    data = subprocess.check_output(["augtool"] + get_transformations() + ["--include=lenses", "dump-xml", "/files"])

    root = ET.fromstring(data)

    def walk_nodes(root):
        ''' Performs recursive pre-order depth-first traversal of tree from `root` node
            first using `_rec` function to drill down to a valid path and then
            `_rec_props` to obtain whole property sub-tree.

            The original XML preserves order of declaration so we do as well and hence use
            `OrderedDict` for dictionaries here. Preserving order of declarations is important
            because we want to see configuration directives in the same order they were declared in
            to figure out which one is valid.
        '''

        data = OrderedDict()

        def _rec_props(n):
            ''' Recursive property function '''
            label = n.attrib['label']

            # Skip comments for now
            if label == '#comment':
                return

            props = OrderedDict()
            props['name'] = label

            value = n.find('value')
            if value is not None:
                props['value'] = value.text


            sub = [v for v in (_rec_props(node) for node in n.findall('node')) if v]

            if sub:
                # Do not output empty properties to save space
                props['properties'] = sub

            return props

        def _rec(n, curpath=''):
            ''' Recursive file path function  '''
            if 'label' in n.attrib:
                curpath += '/' + n.attrib['label']

            is_file = os.path.isfile(curpath)

            if is_file:
                lens = find_lens(curpath, lens_data)
                name = 'aug_' + lens.name.lower()
                if name in data:
                    data[name].append(_rec_props(n))
                else:
                    data[name] = [_rec_props(n)]
                data[name][-1]['absolute_path'] = curpath

            else:
                for c in n:
                    _rec(c, curpath)


        for node in root.findall("./node[@path='/files']/node"):
            _rec(node)

        return data

    return walk_nodes(root)


metadata = process_augeas_data(get_lenses())
print(json.dumps(metadata))
