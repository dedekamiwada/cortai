# Gera dist/Cortai/Cortai.exe. Use o build.bat, que prepara o ambiente.
import shutil
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

site = Path(sysconfig.get_paths()["purelib"])

# Não empacota código no meio de um merge/rebase: um build feito com marcas de conflito no app.js
# já gerou um Cortai.exe com a tela "Cortes com IA" travada (o JS nem carrega).
_conflicts = [
    f"{p.as_posix()}:{i}"
    for p in [*Path("cortai").glob("*.py"), *Path("static").glob("*.*"), Path("run.py")]
    if p.suffix in (".py", ".js", ".html", ".css")
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
    if line.startswith(("<<<<<<< ", ">>>>>>> ")) or line == "======="
]
if _conflicts:
    raise SystemExit("Build cancelado: há marcas de conflito do git (termine o merge/rebase antes):\n  "
                     + "\n  ".join(_conflicts[:20]))

datas = [("static", "static")]
for tool in ("ffmpeg", "ffprobe"):
    found = shutil.which(tool)
    if not found:
        raise SystemExit(f"{tool} precisa estar no PATH para gerar a versão portátil")
    datas.append((found, "bin"))

# CUDA do Whisper: nenhum módulo importa estas DLLs, o ctranslate2 as carrega em tempo de execução.
# Só estas são usadas ao transcrever; as demais do pacote somariam 1,3 GB. Se faltar alguma
# em outra GPU, a transcrição cai para a CPU e o job avisa.
for dll in ("cublas/bin/cublas64_12.dll", "cublas/bin/cublasLt64_12.dll", "cudnn/bin/cudnn64_9.dll"):
    datas.append((str(site / "nvidia" / dll), str(Path("nvidia") / Path(dll).parent)))

datas += collect_data_files("faster_whisper")  # modelo do VAD
datas += collect_data_files("cv2", includes=["data/haarcascade_frontalface_default.xml"])

a = Analysis(
    ["run.py"],
    datas=datas,
    binaries=collect_dynamic_libs("ctranslate2"),
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "PIL"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Cortai", icon="assets/cortai.ico", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Cortai", upx=False)
