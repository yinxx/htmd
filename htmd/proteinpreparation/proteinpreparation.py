import logging
import tempfile
import numpy as np
from pdb2pqr.main import runPDB2PQR
from pdb2pqr.src.pdb import readPDB


# If necessary: http://stackoverflow.com/questions/16981921/relative-imports-in-python-3
from htmd.proteinpreparation.residuedata import ResidueData
from htmd.molecule.molecule import Molecule

logger = logging.getLogger(__name__)


def _selToHoldList(mol, sel):
    if sel:
        tx = mol.copy()
        tx.filter(sel)
        tx.filter("name CA")
        ret = list(zip(tx.resid, tx.chain, tx.insertion))
    else:
        ret = None
    return ret


def _fillMolecule(name, resname, chain, resid, insertion, coords, segid, elements):
    numAtoms = len(name)
    mol = Molecule()
    mol.empty(numAtoms)

    mol.name = np.array(name, dtype=mol._pdb_fields['name'])
    mol.resname = np.array(resname, dtype=mol._pdb_fields['resname'])
    mol.chain = np.array(chain, dtype=mol._pdb_fields['chain'])
    mol.resid = np.array(resid, dtype=mol._pdb_fields['resid'])
    mol.insertion = np.array(insertion, dtype=mol._pdb_fields['insertion'])
    mol.coords = np.array(np.atleast_3d(np.vstack(coords)), dtype=mol._pdb_fields['coords'])
    mol.segid = np.array(segid, dtype=mol._pdb_fields['segid'])
    mol.element = np.array(elements, dtype=mol._pdb_fields['element'])
    return mol


def _fixupWaterNames(mol):
    """Rename WAT OW HW HW atoms as O H1 H2"""
    mol.set("name","O",sel="resname WAT and name OW")
    hw = mol.atomselect("resname WAT and name HW")
    n = np.arange(len(hw))
    hw_even = np.logical_and(hw, np.mod(n, 2) == 0)
    hw_odd  = np.logical_and(hw, np.mod(n, 2) == 1)
    mol.set("name", "H1",sel=hw_even)
    mol.set("name", "H2",sel=hw_odd)



def prepareProtein(mol_in,
                   pH=7.0,
                   verbose=0,
                   returnDetails=False,
                   hydrophobicThickness=None,
                   holdSelection=None):
    """A system preparation wizard for HTMD.

    Returns a Molecule object, where residues have been renamed to follow
    internal conventions on protonation (below). Coordinates are changed to
    optimize the H-bonding network. This should be roughly equivalent to mdweb and Maestro's
    preparation wizard.

    The following residue names are used in the returned molecule:

        ASH 	Neutral ASP
        CYX 	SS-bonded CYS
        CYM 	Negative CYS
        GLH 	Neutral GLU
        HIP 	Positive HIS
        HID 	Neutral HIS, proton HD1 present
        HIE 	Neutral HIS, proton HE2 present
        LYN 	Neutral LYS
        TYM 	Negative TYR
        AR0     Neutral ARG

    If hydrophobicThickness is set to a positive value 2*h, a warning is produced for titratable residues
    having -h<z<h and are buried in the protein by less than 75%. The list of such residues can be accessed setting
    returnDetails to True. Note that the heuristic for the detection of membrane-exposed residues is very crude;
    the "buried fraction" computation (from propka) is approximate; also, in the presence of cavities,
    residues may be solvent-exposed independently from their z location.


    Notes
    -----
    In case of problems, exclude water and other dummy atoms.


    Features
    --------
     - assign protonation states via propKa
     - flip residues to optimize H-bonding network
     - debump collisions
     - fill-in missing atoms, e.g. hydrogen atoms


    Parameters
    ----------
    mol_in : htmd.Molecule
        the object to be optimized
    pH : float
        pH to decide titration
    verbose : int
        verbosity
    returnDetails : bool
        whether to return just the prepared Molecule (False, default) or a molecule *and* a ResidueInfo
        object including computed properties
    hydrophobicThickness : float
        the thickness of the membrane in which the protein is embedded, or None if globular protein.
        Used to provide a warning about membrane-exposed residues.
    holdSelection : str
        Atom selection to be excluded from optimization.
        Only the carbon-alpha atom will be considered for the corresponding residue.


    Returns
    -------
    mol_out : Molecule
        the molecule titrated and optimized. The molecule object contains an additional attribute,
    resData : ResidueData
        a table of residues with the corresponding protonation states, pKas, and other information


    Examples
    --------
    >>> tryp = Molecule('3PTB')

    >>> tryp_op, prepData = prepareProtein(tryp, returnDetails=True)
    >>> tryp_op.write('proteinpreparation-test-main-ph-7.pdb')
    >>> prepData.data.to_excel("/tmp/tryp-report.xlsx")
    >>> prepData
    ResidueData object about 290 residues.
    Unparametrized residue names: CA, BEN
    Please find the full info in the .data property, e.g.:
      resname  resid insertion chain       pKa protonation flipped     buried
    0     ILE     16               A       NaN         ILE     NaN        NaN
    1     VAL     17               A       NaN         VAL     NaN        NaN
    2     GLY     18               A       NaN         GLY     NaN        NaN
    3     GLY     19               A       NaN         GLY     NaN        NaN
    4     TYR     20               A  9.590845         TYR     NaN  14.642857
     . . .
    >>> x_HIE91_ND1 = tryp_op.get("coords","resid 91 and  name ND1")
    >>> x_SER93_H =   tryp_op.get("coords","resid 93 and  name H")
    >>> len(x_SER93_H) == 3
    True
    >>> np.linalg.norm(x_HIE91_ND1-x_SER93_H) < 3
    True

    >>> tryp_op = prepareProtein(tryp, pH=1.0)
    >>> tryp_op.write('proteinpreparation-test-main-ph-1.pdb')

    >>> tryp_op = prepareProtein(tryp, pH=14.0)
    >>> tryp_op.write('proteinpreparation-test-main-ph-14.pdb')

    >>> mol = Molecule("1r1j")
    >>> mo, prepData = prepareProtein(mol, returnDetails=True)
    >>> prepData.missedLigands
    ['NAG', 'ZN', 'OIR']

    >>> his = prepData.data.resname == "HIS"
    >>> prepData.data[his][["resid","insertion","chain","resname","protonation"]]
         resid insertion chain resname protonation
    160    214               A     HIS         HID
    163    217               A     HIS         HID
    383    437               A     HIS         HID
    529    583               A     HIS         HID
    533    587               A     HIS         HIP
    583    637               A     HIS         HID
    627    681               A     HIS         HID
    657    711               A     HIS         HIP
    679    733               A     HIS         HID

    >>> mor = Molecule("4dkl")
    >>> mor.filter("protein and noh")
    >>> mor_opt, mor_data = prepareProtein(mor, returnDetails=True, 
    ...                                    hydrophobicThickness=32.0)
    >>> exposedRes = mor_data.data.membraneExposed
    >>> mor_data.data[exposedRes].to_excel("/tmp/mor_exposed_residues.xlsx")

    >>> im=Molecule("4bkj")
    >>> imo,imd=prepareProtein(im,returnDetails=True)
    >>> imd.data.to_excel("/tmp/imatinib_report.xlsx")


    See Also
    --------
    The ResidueData object.


    Unsupported/To Do/To Check
    --------------------------
     - ligands
     - termini
     - force residues
     - multiple chains
     - nucleic acids
     - reporting in machine-readable form
     - coupled titrating residues
     - Disulfide bridge detection (implemented but unused)

    """

    oldLoggingLevel = logger.level
    if verbose:
        logger.setLevel(logging.DEBUG)
    logger.debug("Starting.")

    # We could transform the molecule into an internal object, but for
    # now I prefer to rely on the strange internal parser to avoid
    # hidden quirks.
    tmpin = tempfile.NamedTemporaryFile(suffix=".pdb", mode="w+")
    logger.debug("Temporary file is " + tmpin.name)
    mol_in.write(tmpin.name)  # Not sure this is sound unix

    pdblist, errlist = readPDB(tmpin)
    if len(pdblist) == 0 and len(errlist) == 0:
        raise Exception('Internal error in preparing input to pdb2pqr')

    # We could set additional options here
    import propka.lib

    # An ugly hack to silence non-prefixed logging messages
    for h in propka.lib.logger.handlers:
        if h.formatter._fmt == '%(message)s':
            propka.lib.logger.removeHandler(h)

    propka_opts, dummy = propka.lib.loadOptions('--quiet')
    propka_opts.verbosity = verbose
    propka_opts.verbose = verbose  # Will be removed in future propKas

    # Note on naming. The behavior of PDB2PQR is controlled by two
    # parameters, ff and ffout. My understanding is that the ff
    # parameter sets which residues are SUPPORTED by the underlying
    # FF, PLUS the charge and radii.  The ffout parameter sets the
    # naming scheme. Therefore, I want ff to be as general as
    # possible, which turns out to be "parse". Then I pick a
    # convenient ffout.

    # Hold list (None -> None)
    hlist = _selToHoldList(mol_in, holdSelection)


    # Relying on defaults
    header, pqr, missedLigands, pdb2pqr_protein = runPDB2PQR(pdblist,
                                                               ph=pH, verbose=verbose,
                                                               ff="parse", ffout="amber",
                                                               ph_calc_method="propka31",
                                                               ph_calc_options=propka_opts,
                                                               holdList=hlist)
    tmpin.close()

    # Diagnostics
    for missedligand in missedLigands:
        logger.warning("The following residue has not been optimized: " + missedligand)

    # Here I parse the returned protein object and recreate a Molecule,
    # because I need to access the properties.
    logger.debug("Building Molecule object.")

    name = []
    resid = []
    chain = []
    insertion = []
    coords = []
    resname = []
    segids = []
    elements = []

    resData = ResidueData()

    resData.header = header
    resData.pqr = pqr

    for residue in pdb2pqr_protein.residues:
        # if 'ffname' in residue.__dict__:
        if getattr(residue,'ffname',None):
            curr_resname = residue.ffname
            if len(curr_resname) >= 4:
                curr_resname = curr_resname[-3:]
                logger.debug("Residue %s has internal name %s, replacing with %s" %
                            (residue, residue.ffname, curr_resname))
        else:
            curr_resname = residue.name

        resData._setProtonationState(residue, curr_resname)

        #if 'patches' in residue.__dict__:
        if getattr(residue, 'patches', None):
            for patch in residue.patches:
                resData._appendPatches(residue, patch)
                if patch != "PEPTIDE":
                    logger.debug("Residue %s has patch %s set" % (residue, patch))

        if getattr(residue, 'wasFlipped', 'UNDEF') != 'UNDEF':
            resData._setFlipped(residue, residue.wasFlipped)


        for atom in residue.atoms:
            name.append(atom.name)
            resid.append(residue.resSeq)
            chain.append(residue.chainID)
            insertion.append(residue.iCode)
            coords.append([atom.x, atom.y, atom.z])
            resname.append(curr_resname)
            segids.append(atom.segID)
            elements.append(atom.element)

    mol_out = _fillMolecule(name, resname, chain, resid, insertion, coords, segids, elements)
    _fixupWaterNames(mol_out)

    # Return residue information
    resData._importPKAs(pdb2pqr_protein.pka_protein)
    resData.pdb2pqr_protein = pdb2pqr_protein
    resData.missedLigands = missedLigands

    resData._warnIfpKCloseTopH(pH)

    if hydrophobicThickness:
        resData._setMembraneExposureAndWarn(hydrophobicThickness)

    logger.debug("Returning.")
    logger.setLevel(oldLoggingLevel)

    if returnDetails:
        return mol_out, resData
    else:
        return mol_out




# A test method
if __name__ == "__main__":
    from htmd import home
    import os
    import sys

    if len(sys.argv) > 1:
        # Reproducibility test
        # rm mol-test-*; for i in `seq 9`; do py ./proteinpreparation.py ./1r1j.pdb > mol-test-$i.log ; cp ./mol-test.pdb mol-test-$i.pdb; cp mol-test.csv mol-test-$i.csv ; done
        mol = Molecule(sys.argv[1])
        mol.filter("protein")
        mol_op, prepData = prepareProtein(mol, returnDetails=True)
        mol_op.write("./mol-test.pdb")
        prepData.data.to_excel("./mol-test.xlsx")
        prepData.data.to_csv("./mol-test.csv")

        """
        x_HIS91_ND1 = tryp_op.get("coords","resid 91 and  name ND1")
        x_SER93_H =   tryp_op.get("coords","resid 93 and  name H")
        assert len(x_SER93_H) == 3
        assert np.linalg.norm(x_HIS91_ND1-x_SER93_H) > 2
        assert tryp_op.get("resname","resid 91 and  name CA") == "HIE"
        """

    else:
        import doctest
        doctest.testmod()