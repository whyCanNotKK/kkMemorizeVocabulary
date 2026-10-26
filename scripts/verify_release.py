"""Validate the onedir bundle against current sources and reject foreign system DLLs."""
import hashlib
from pathlib import Path
import types

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]


def signature(code):
    return (code.co_name, code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount,
            code.co_flags, code.co_code, code.co_names, code.co_varnames,
            code.co_cellvars, code.co_freevars,
            tuple(signature(value) if isinstance(value, types.CodeType) else value for value in code.co_consts))


def main():
    directory = ROOT / "dist" / "kkMemorizeVocabulary"
    executable = directory / "kkMemorizeVocabulary.exe"
    assert executable.is_file(), "EXE is missing"
    bad = [p for p in directory.rglob("*.dll")
           if p.name.lower().startswith(("api-ms-win-", "ext-ms-", "icudt"))
           or p.name.lower() in {"icuuc.dll", "icuin.dll"}]
    assert not bad, f"Unexpected foreign Windows system/ICU DLLs: {bad}"
    archive = CArchiveReader(str(executable)).open_embedded_archive("PYZ.pyz")
    checked = []
    for path in sorted((ROOT / "vocabulary").glob("*.py")):
        name = "vocabulary" if path.stem == "__init__" else f"vocabulary.{path.stem}"
        packaged = archive.extract(name)
        source = compile(path.read_text(encoding="utf-8"), str(path), "exec", dont_inherit=True, optimize=0)
        assert signature(packaged) == signature(source), f"Stale packaged module: {name}"
        checked.append(name)
    print(f"Verified {len(checked)} packaged modules match source; no foreign ICU/API-set DLLs.")
    print(f"EXE SHA256: {hashlib.sha256(executable.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
