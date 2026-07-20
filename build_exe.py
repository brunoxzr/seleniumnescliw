"""Builda o MavioRobot.exe preservando .env/cnpjs.csv/data/ que já existem em
prod/MavioRobot/ — o usuário edita esses arquivos direto ali (progresso salvo,
credenciais, lista de CNPJs) e o COLLECT do PyInstaller apaga a pasta de saída
inteira antes de reconstruir, então o backup precisa acontecer ANTES do build,
não depois (copiar só depois já é tarde: o que estava lá já foi destruído).

O backup fica em .build_backup/ (fora de prod/, sobrevive mesmo se o build
falhar no meio) e só é restaurado — nunca apagado — ao final.

Uso: python build_exe.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DIST_DIR = ROOT / "prod" / "MavioRobot"
BACKUP_DIR = ROOT / ".build_backup"
PRESERVE = [".env", "cnpjs.csv", "data"]


def _copy(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def main() -> None:
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)
    BACKUP_DIR.mkdir()

    for name in PRESERVE:
        src = DIST_DIR / name
        if src.exists():
            _copy(src, BACKUP_DIR / name)
            print(f"backup: {name}")

    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "MavioRobot.spec",
             "--distpath", "prod", "--workpath", "build", "--noconfirm"],
            cwd=ROOT, check=True,
        )
    finally:
        # restaura mesmo se o PyInstaller falhar no meio — nunca deixa o
        # usuário sem os arquivos que já tinha, mesmo com build quebrado
        for name in PRESERVE:
            backup_src = BACKUP_DIR / name
            dst = DIST_DIR / name
            source = backup_src if backup_src.exists() else (ROOT / name)
            if source.exists():
                _copy(source, dst)
                print(f"restaurado: {name}")

    shutil.rmtree(BACKUP_DIR)


if __name__ == "__main__":
    main()
