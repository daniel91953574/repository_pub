import os
import subprocess
import tempfile
from PyQt6.QtCore import QThread, pyqtSignal

# Get the absolute path to the pyNASM root folder (parent of 'core')
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NASM_BIN = os.path.join(ROOT_DIR, "nasm", "nasm.exe")
GOLINK_BIN = os.path.join(ROOT_DIR, "nasm", "GoLink.exe")

class CompilerWorker(QThread):
    log_signal = pyqtSignal(str, str) # message, color (info, success, error)
    finished_signal = pyqtSignal(bool, str) # True if success, string is the path to the executable

    def __init__(self, code_content, config):
        """
        config = {
            'target': 'console' | 'gui' | 'obj' | 'bios' | 'uefi',
            'entry_point': 'main',
            'dlls': ['msvcrt.dll', ...],
            'extra_files': ['icon.res', 'math.obj', ...],
            'output_dir': 'C:\\path\\to\\dir' (or None for temp),
            'base_name': 'myprogram' (or None for 'output')
        }
        """
        super().__init__()
        self.code_content = code_content
        self.config = config

        self.output_dir = config.get("output_dir")
        if not self.output_dir:
            self.output_dir = tempfile.mkdtemp()
            
        self.base_name = config.get("base_name") or "output"
        
        self.asm_file = os.path.join(self.output_dir, f"{self.base_name}.asm")
        self.obj_file = os.path.join(self.output_dir, f"{self.base_name}.obj")
        
        target = self.config.get("target", "console")
        if target == "bios":
            self.out_ext = ".bin"
        elif target == "obj":
            self.out_ext = ".obj"
        elif target == "uefi":
            self.out_ext = ".efi"
        else:
            self.out_ext = ".exe"
            
        self.final_file = os.path.join(self.output_dir, f"{self.base_name}{self.out_ext}")

    def run(self):
        try:
            if not os.path.exists(self.asm_file):
                with open(self.asm_file, "w", encoding="utf-8") as f:
                    f.write(self.code_content)
            
            self.log_signal.emit(f"[*] Starting build process in {self.output_dir}", "info")
            
            target = self.config.get("target", "console")
            
            # --- STEP 1: Run NASM ---
            if target == "bios":
                nasm_cmd = [NASM_BIN, "-f", "bin", self.asm_file, "-o", self.final_file]
            else:
                nasm_cmd = [NASM_BIN, "-f", "win64", self.asm_file, "-o", self.obj_file]
                
            self.log_signal.emit(f"[>] {' '.join(nasm_cmd)}", "info")
            result = subprocess.run(nasm_cmd, capture_output=True, text=True)
            
            if result.stdout: self.log_signal.emit(result.stdout, "info")
            if result.stderr: self.log_signal.emit(result.stderr, "error")
                
            if result.returncode != 0:
                self.log_signal.emit(f"[-] NASM compilation failed with code {result.returncode}.", "error")
                self.finished_signal.emit(False, "")
                return

            self.log_signal.emit("[+] NASM compiled successfully.", "success")
            if target in ["bios", "obj"]:
                self.log_signal.emit(f"[+] Build successful! Output: {self.final_file}", "success")
                self.finished_signal.emit(True, self.final_file)
                return

            # --- STEP 2: Linker ---
            entry = self.config.get("entry_point", "main")
            extra_files = self.config.get("extra_files", [])
            dlls = self.config.get("dlls", [])
            
            if target == "uefi":
                link_cmd = [
                    "link.exe", 
                    f"/subsystem:efi_application", 
                    f"/entry:{entry}", 
                    f"/out:{self.final_file}",
                    self.obj_file
                ] + extra_files
                linker_name = "link.exe (MSVC)"
            else:
                # console or gui using GoLink
                linker_name = "GoLink.exe"
                subsystem = "/console" if target == "console" else ""
                link_cmd = [GOLINK_BIN, f"/entry:{entry}"]
                if subsystem:
                    link_cmd.append(subsystem)
                link_cmd.append(self.obj_file)
                link_cmd.extend(dlls)
                link_cmd.extend(extra_files)
                
            self.log_signal.emit(f"[>] {' '.join(link_cmd)}", "info")
            result2 = subprocess.run(link_cmd, capture_output=True, text=True)
            
            if result2.stdout: self.log_signal.emit(result2.stdout, "info")
            if result2.stderr: self.log_signal.emit(result2.stderr, "error")
                
            if result2.returncode != 0:
                self.log_signal.emit(f"[-] {linker_name} failed with code {result2.returncode}.", "error")
                self.finished_signal.emit(False, "")
                return

            self.log_signal.emit(f"[+] Build successful! Output: {self.final_file}", "success")
            self.finished_signal.emit(True, self.final_file)

        except FileNotFoundError as e:
            self.log_signal.emit(f"[-] Missing compiler tool: {e}", "error")
            self.log_signal.emit("[-] Please ensure 'nasm' and 'golink' (or 'link') are in the ./nasm folder or PATH.", "error")
            self.finished_signal.emit(False, "")
        except Exception as e:
            self.log_signal.emit(f"[-] Unexpected error: {str(e)}", "error")
            self.finished_signal.emit(False, "")
