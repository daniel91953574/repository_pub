import re
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QPushButton, QListWidget, QFileDialog, QGroupBox)
from PyQt6.QtCore import Qt

class LinkerDialog(QDialog):
    def __init__(self, code_content, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ Advanced Linker Options")
        self.resize(500, 400)
        
        self.code_content = code_content
        
        self.dlls = []
        self.extra_files = []
        
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # --- DLLs Section ---
        group_dlls = QGroupBox("Dynamic Link Libraries (DLLs)")
        layout_dlls = QVBoxLayout(group_dlls)
        
        self.list_dlls = QListWidget()
        layout_dlls.addWidget(self.list_dlls)
        
        btn_layout_dlls = QHBoxLayout()
        btn_add_dll = QPushButton("➕ Add DLL")
        btn_add_dll.clicked.connect(self.add_dll)
        btn_remove_dll = QPushButton("➖ Remove Selected")
        btn_remove_dll.clicked.connect(lambda: self.remove_selected(self.list_dlls))
        btn_auto_detect = QPushButton("🔍 Auto-Detect Imports")
        btn_auto_detect.clicked.connect(self.auto_detect_imports)
        
        btn_layout_dlls.addWidget(btn_add_dll)
        btn_layout_dlls.addWidget(btn_remove_dll)
        btn_layout_dlls.addWidget(btn_auto_detect)
        layout_dlls.addLayout(btn_layout_dlls)
        
        layout.addWidget(group_dlls)
        
        # --- Extra Files (.obj, .res) ---
        group_extra = QGroupBox("Extra Files (.obj, .res)")
        layout_extra = QVBoxLayout(group_extra)
        
        self.list_extra = QListWidget()
        layout_extra.addWidget(self.list_extra)
        
        btn_layout_extra = QHBoxLayout()
        btn_add_extra = QPushButton("➕ Add File")
        btn_add_extra.clicked.connect(self.add_extra_file)
        btn_remove_extra = QPushButton("➖ Remove Selected")
        btn_remove_extra.clicked.connect(lambda: self.remove_selected(self.list_extra))
        
        btn_layout_extra.addWidget(btn_add_extra)
        btn_layout_extra.addWidget(btn_remove_extra)
        layout_extra.addLayout(btn_layout_extra)
        
        layout.addWidget(group_extra)
        
        # --- OK / Cancel ---
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def add_dll(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select DLL", "C:\\Windows\\System32", "DLL Files (*.dll);;All Files (*)")
        if path:
            self.list_dlls.addItem(path)

    def add_extra_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Object or Resource", "", "Object/Resource Files (*.obj *.res);;All Files (*)")
        if path:
            self.list_extra.addItem(path)

    def remove_selected(self, list_widget):
        for item in list_widget.selectedItems():
            list_widget.takeItem(list_widget.row(item))

    def auto_detect_imports(self):
        # Basic heuristic mapping common API functions to their DLLs
        common_dlls = {
            "printf": "msvcrt.dll", "scanf": "msvcrt.dll", "puts": "msvcrt.dll", "exit": "msvcrt.dll",
            "MessageBoxA": "user32.dll", "MessageBoxW": "user32.dll", "CreateWindowExA": "user32.dll",
            "GetMessageA": "user32.dll", "DispatchMessageA": "user32.dll", "DefWindowProcA": "user32.dll",
            "PostQuitMessage": "user32.dll", "ShowWindow": "user32.dll", "UpdateWindow": "user32.dll",
            "RegisterClassExA": "user32.dll", "LoadCursorA": "user32.dll", "LoadIconA": "user32.dll",
            "ExitProcess": "kernel32.dll", "GetStdHandle": "kernel32.dll", "WriteConsoleA": "kernel32.dll",
            "GetModuleHandleA": "kernel32.dll", "Sleep": "kernel32.dll",
            "CreateSolidBrush": "gdi32.dll", "TextOutA": "gdi32.dll", "SelectObject": "gdi32.dll"
        }
        
        detected = set()
        externs = re.findall(r'^\s*extern\s+([a-zA-Z0-9_, \t]+)', self.code_content, re.MULTILINE)
        for ext_line in externs:
            funcs = [f.strip() for f in ext_line.split(',')]
            for func in funcs:
                if func in common_dlls:
                    detected.add(common_dlls[func])
                    
        # Add to list if not already there
        existing = [self.list_dlls.item(i).text() for i in range(self.list_dlls.count())]
        for d in detected:
            if d not in existing:
                self.list_dlls.addItem(d)

    def get_dlls(self):
        return [self.list_dlls.item(i).text() for i in range(self.list_dlls.count())]

    def get_extra_files(self):
        return [self.list_extra.item(i).text() for i in range(self.list_extra.count())]
