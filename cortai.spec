# Gera dist/Cortai/Cortai.exe. Use o build.bat, que prepara o ambiente.
import shutil
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

site = Path(sysconfig.get_paths()["purelib"])

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
