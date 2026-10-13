@echo off
REM Build SciSketch.exe for Windows:  build.bat   ->  dist\SciSketch\SciSketch.exe
pip install -r requirements.txt -r requirements-build.txt || exit /b 1
pyinstaller scisketch.spec --noconfirm --clean || exit /b 1
echo Built: dist\SciSketch\SciSketch.exe
