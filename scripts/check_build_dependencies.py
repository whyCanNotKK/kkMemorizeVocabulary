"""Check the lock without accessing the network on subsequent builds."""
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def main():
    mismatches = []
    lock = Path(__file__).resolve().parents[1] / "requirements.lock.txt"
    for line in lock.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, expected = line.strip().split("==", 1)
        try:
            installed = version(name)
        except PackageNotFoundError:
            installed = "missing"
        if installed != expected:
            mismatches.append(f"{name}: {installed}, expected {expected}")
    if mismatches:
        print("\n".join(mismatches))
        return 1
    print("Locked build dependencies are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
