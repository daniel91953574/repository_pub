import sys
import os
import subprocess
import re
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPlainTextEdit, QTextEdit, QPushButton, QLabel, QSplitter, 
                             QMenuBar, QMenu, QFileDialog, QCompleter, QToolBar, QComboBox, 
                             QToolButton, QDockWidget)
from PyQt6.QtGui import QColor, QPainter, QFont, QTextFormat, QTextCursor, QAction, QIcon, QKeySequence
from PyQt6.QtCore import Qt, QRect, QSize, pyqtSignal, QStringListModel

from core.database import TEMPLATES, get_all_completions, INSTRUCTIONS, REGISTERS, DIRECTIVES
from core.tutorial import TUTORIAL_STEPS
from core.highlighter import ASMSyntaxHighlighter
from core.compiler import CompilerWorker
from core.linker_dialog import LinkerDialog

# --- Custom Line Number Area ---
class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        self.editor.lineNumberAreaPaintEvent(event)

# --- Minimap (Code Elevator) ---
class MinimapWidget(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setFixedWidth(50)
        self.setMouseTracking(True)
        self.is_dragging = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(20, 20, 30)) # Minimap background

        # Get lines
        doc = self.editor.document()
        block = doc.firstBlock()
        
        # Calculate line height (2 pixels per line)
        line_h = 2
        total_lines = doc.blockCount()
        
        # Draw tiny blocks representing text
        y = 0
        painter.setPen(Qt.PenStyle.NoPen)
        while block.isValid() and y <= self.height():
            text = block.text().lstrip()
            length = min(len(text), 23) # Max width ~ 46px
            if length > 0:
                # Basic coloring for minimap
                if text.startswith(';'):
                    painter.setBrush(QColor(80, 140, 80)) # Comment
                elif ':' in text:
                    painter.setBrush(QColor(255, 200, 80)) # Label
                elif text.startswith('%') or text.startswith('@'):
                    painter.setBrush(QColor(160, 120, 255)) # Directive/Macro
                else:
                    painter.setBrush(QColor(100, 180, 255)) # Code
                
                painter.drawRect(2, y, length * 2, line_h)
            block = block.next()
            y += line_h
            
        # Draw viewport slider overlay
        scrollbar = self.editor.verticalScrollBar()
        total_scroll = scrollbar.maximum() + scrollbar.pageStep()
        if total_scroll > 0:
            ratio = y / total_scroll
            slider_y = scrollbar.value() * ratio
            slider_h = scrollbar.pageStep() * ratio
            
            painter.setBrush(QColor(255, 255, 255, 30)) # Transparent white
            painter.drawRect(0, int(slider_y), 50, max(10, int(slider_h)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = True
            self.scroll_to_mouse(event.position().y())

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            self.scroll_to_mouse(event.position().y())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_dragging = False

    def scroll_to_mouse(self, my):
        doc = self.editor.document()
        line_h = 2
        total_h = doc.blockCount() * line_h
        if total_h == 0: return
        
        ratio = my / total_h
        scrollbar = self.editor.verticalScrollBar()
        total_scroll = scrollbar.maximum() + scrollbar.pageStep()
        
        val = int(ratio * total_scroll - scrollbar.pageStep() / 2)
        scrollbar.setValue(max(0, min(val, scrollbar.maximum())))

# --- Robust Code Editor ---
class CodeEditor(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.line_number_area = LineNumberArea(self)
        self.minimap = MinimapWidget(self)
        
        self.blockCountChanged.connect(self.updateLineNumberAreaWidth)
        self.updateRequest.connect(self.updateLineNumberArea)
        self.cursorPositionChanged.connect(self.highlightCurrentLine)
        
        self.verticalScrollBar().valueChanged.connect(self.minimap.update)
        self.document().contentsChange.connect(self.minimap.update)
        
        self.updateLineNumberAreaWidth(0)
        self.highlightCurrentLine()
        
        # Font settings - CONSOLAS SIZE 8
        font = QFont("Consolas", 8)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

        # Autocompletion
        self.completer = None
        
        # Tab settings
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * 4)

    def setCompleter(self, completer):
        if self.completer:
            self.completer.disconnect(self)
        self.completer = completer
        if not self.completer:
            return
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated.connect(self.insertCompletion)

    def insertCompletion(self, completion):
        if self.completer.widget() != self:
            return
        tc = self.textCursor()
        extra = len(completion) - len(self.completer.completionPrefix())
        tc.movePosition(QTextCursor.MoveOperation.Left)
        tc.movePosition(QTextCursor.MoveOperation.EndOfWord)
        tc.insertText(completion[-extra:])
        self.setTextCursor(tc)

    def textUnderCursor(self):
        tc = self.textCursor()
        tc.select(QTextCursor.SelectionType.WordUnderCursor)
        return tc.selectedText()

    def keyPressEvent(self, e):
        if self.completer and self.completer.popup().isVisible():
            if e.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Escape, Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                e.ignore()
                return

        super().keyPressEvent(e)

        ctrl_or_shift = e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        if not self.completer or (ctrl_or_shift and not e.text()):
            return

        eow = "~!@#$%^&*()_+{}|:\"<>?,./;'[]\\-=" 
        has_modifier = (e.modifiers() != Qt.KeyboardModifier.NoModifier) and not ctrl_or_shift
        completion_prefix = self.textUnderCursor()

        if not e.text() or len(completion_prefix) < 2 or e.text()[-1] in eow:
            self.completer.popup().hide()
            return

        if completion_prefix != self.completer.completionPrefix():
            self.completer.setCompletionPrefix(completion_prefix)
            self.completer.popup().setCurrentIndex(self.completer.completionModel().index(0, 0))

        cr = self.cursorRect()
        cr.setWidth(self.completer.popup().sizeHintForColumn(0) + self.completer.popup().verticalScrollBar().sizeHint().width())
        self.completer.complete(cr)

    def lineNumberAreaWidth(self):
        digits = 1
        max_blocks = max(1, self.blockCount())
        while max_blocks >= 10:
            max_blocks /= 10
            digits += 1
        space = 8 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def updateLineNumberAreaWidth(self, _):
        # 50px right margin for minimap
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 50, 0)

    def updateLineNumberArea(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.updateLineNumberAreaWidth(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))
        self.minimap.setGeometry(QRect(cr.right() - 50, cr.top(), 50, cr.height()))

    def highlightCurrentLine(self):
        extra_selections = []
        if not self.isReadOnly():
            selection = QTextEdit.ExtraSelection()
            line_color = QColor(30, 30, 42)
            selection.format.setBackground(line_color)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def lineNumberAreaPaintEvent(self, event):
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor(24, 24, 34))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor(100, 100, 120))
                painter.drawText(0, top, self.line_number_area.width() - 4, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, number)
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

# --- Tutorial Dock ---
class TutorialDock(QDockWidget):
    def __init__(self, editor, parent=None):
        super().__init__("📖 Tutorial Guiado", parent)
        self.editor = editor
        self.current_step = 0
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea)
        
        self.init_ui()
        self.load_step()

    def init_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        
        self.title_lbl = QLabel()
        self.title_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.title_lbl.setWordWrap(True)
        layout.addWidget(self.title_lbl)
        
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Segoe UI", 9))
        layout.addWidget(self.text_edit)
        
        btn_layout = QHBoxLayout()
        self.btn_prev = QPushButton("⬅️ Voltar")
        self.btn_prev.clicked.connect(self.prev_step)
        
        self.btn_insert = QPushButton("📥 Inserir Exemplo")
        self.btn_insert.clicked.connect(self.insert_code)
        self.btn_insert.setStyleSheet("background-color: #A050D0; color: white; font-weight: bold;")
        
        self.btn_next = QPushButton("Avançar ➡️")
        self.btn_next.clicked.connect(self.next_step)
        
        btn_layout.addWidget(self.btn_prev)
        btn_layout.addWidget(self.btn_insert)
        btn_layout.addWidget(self.btn_next)
        layout.addLayout(btn_layout)
        
        self.setWidget(container)

    def load_step(self):
        step = TUTORIAL_STEPS[self.current_step]
        self.title_lbl.setText(step["title"])
        self.text_edit.setPlainText(step["text"])
        self.btn_prev.setEnabled(self.current_step > 0)
        self.btn_next.setEnabled(self.current_step < len(TUTORIAL_STEPS) - 1)

    def prev_step(self):
        if self.current_step > 0:
            self.current_step -= 1
            self.load_step()

    def next_step(self):
        if self.current_step < len(TUTORIAL_STEPS) - 1:
            self.current_step += 1
            self.load_step()

    def insert_code(self):
        step = TUTORIAL_STEPS[self.current_step]
        self.editor.insertPlainText(step["code"] + "\n")

# --- Main Window ---
class pyNASMStudio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("pyNASM Studio v3.0 (Professional Edition)")
        self.resize(1200, 800)
        self.current_file = None
        self.last_executable = None
        self.linker_dlls = []
        self.linker_extra = []
        
        # Set global font to size 8 for that dense IDE look
        app_font = QFont("Segoe UI", 8)
        QApplication.setFont(app_font)
        
        self.init_ui()
        self.setup_dark_theme()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Editor setup
        self.editor = CodeEditor()
        self.highlighter = ASMSyntaxHighlighter(self.editor.document())
        
        # Completer setup
        word_list = [c["word"] for c in get_all_completions()]
        completer_model = QStringListModel(word_list)
        completer = QCompleter()
        completer.setModel(completer_model)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.editor.setCompleter(completer)
        
        # Terminal setup
        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setFont(QFont("Consolas", 8))
        
        splitter.addWidget(self.editor)
        splitter.addWidget(self.terminal)
        splitter.setSizes([600, 200])
        
        main_layout.addWidget(splitter)
        
        self.create_menus()
        self.create_toolbar()
        
        self.tutorial_dock = TutorialDock(self.editor, self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.tutorial_dock)
        self.tutorial_dock.hide()
        
        self.editor.setPlainText(TEMPLATES["Windows x64 Console EXE"])
        self.terminal.append("[*] Ready. Advanced Build System & Minimap Initialized.")

    def create_menus(self):
        menubar = self.menuBar()
        
        # File Menu
        file_menu = menubar.addMenu("File")
        
        new_act = QAction("New", self)
        new_act.setShortcut(QKeySequence.StandardKey.New)
        new_act.triggered.connect(self.new_file)
        file_menu.addAction(new_act)
        
        open_act = QAction("Open", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self.open_file)
        file_menu.addAction(open_act)
        
        save_act = QAction("Save", self)
        save_act.setShortcut(QKeySequence.StandardKey.Save)
        save_act.triggered.connect(self.save_file)
        file_menu.addAction(save_act)
        
        save_as_act = QAction("Save As...", self)
        save_as_act.triggered.connect(self.save_file_as)
        file_menu.addAction(save_as_act)

        # Templates Menu
        tpl_menu = menubar.addMenu("Templates")
        for name, code in TEMPLATES.items():
            act = QAction(name, self)
            act.triggered.connect(lambda checked, c=code: self.editor.setPlainText(c))
            tpl_menu.addAction(act)

    def create_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        self.addToolBar(toolbar)
        
        self.target_combo = QComboBox()
        self.target_combo.addItems(["Win64 Console EXE", "Win64 GUI EXE", "OBJ Only", "BIOS (16-bit BIN)", "UEFI (64-bit EFI)"])
        toolbar.addWidget(self.target_combo)
        
        opt_btn = QPushButton("⚙️ Linker Options")
        opt_btn.clicked.connect(self.open_linker_dialog)
        toolbar.addWidget(opt_btn)
        
        build_btn = QPushButton("▶ Compile")
        build_btn.setStyleSheet("background-color: #3C8A5A; color: white; font-weight: bold; padding: 4px 10px; border-radius: 2px;")
        build_btn.clicked.connect(self.compile_code)
        toolbar.addWidget(build_btn)
        
        self.run_btn = QPushButton("🚀 Run")
        self.run_btn.setStyleSheet("background-color: #3C5A8A; color: white; font-weight: bold; padding: 4px 10px; border-radius: 2px;")
        self.run_btn.clicked.connect(self.run_executable)
        self.run_btn.setEnabled(False)
        toolbar.addWidget(self.run_btn)
        
        toolbar.addSeparator()
        
        tut_btn = QPushButton("📖 Tutorial Guiado")
        tut_btn.setStyleSheet("background-color: #C87832; color: white; font-weight: bold; padding: 4px 10px; border-radius: 2px;")
        tut_btn.clicked.connect(lambda: self.tutorial_dock.setVisible(not self.tutorial_dock.isVisible()))
        toolbar.addWidget(tut_btn)
        
        toolbar.addSeparator()
        
        # Registrador~Memória Menu
        btn_reg = QToolButton()
        btn_reg.setText("Registrador~Memória")
        btn_reg.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_reg = QMenu()
        
        for group_name, regs in REGISTERS.items():
            group_menu = menu_reg.addMenu(group_name)
            for r in sorted(regs):
                group_menu.addAction(r, lambda checked, x=r: self.editor.insertPlainText(x + ' '))
                
        ptr_menu = menu_reg.addMenu("Pointers")
        for p in ["byte ptr", "word ptr", "dword ptr", "qword ptr", "xmmword ptr", "ymmword ptr", "zmmword ptr"]:
            ptr_menu.addAction(p, lambda checked, x=p: self.editor.insertPlainText(x + ' '))
            
        btn_reg.setMenu(menu_reg)
        toolbar.addWidget(btn_reg)

        # Operador~Função Menu
        btn_op = QToolButton()
        btn_op.setText("Operador~Função")
        btn_op.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_op = QMenu()
        # Grouped to prevent gigantic single menu issue
        sub_menus = {}
        for i in sorted(INSTRUCTIONS.keys()):
            letter = i[0].upper()
            if letter not in sub_menus:
                sub_menus[letter] = menu_op.addMenu(letter)
            sub_menus[letter].addAction(i, lambda checked, x=i: self.editor.insertPlainText(x + ' '))
        btn_op.setMenu(menu_op)
        toolbar.addWidget(btn_op)

        # Outros Menu
        btn_outros = QToolButton()
        btn_outros.setText("Outros")
        btn_outros.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_outros = QMenu()
        for d in sorted(DIRECTIVES):
            menu_outros.addAction(d, lambda checked, x=d: self.editor.insertPlainText(x + ' '))
        btn_outros.setMenu(menu_outros)
        toolbar.addWidget(btn_outros)

    def setup_dark_theme(self):
        qss = """
        QMainWindow { background-color: #121216; }
        QPlainTextEdit {
            background-color: #1A1A24;
            color: #DCDCF0;
            border: none;
            selection-background-color: #264F78;
        }
        QTextEdit {
            background-color: #0F0F14;
            color: #8C8CAA;
            border-top: 2px solid #2D2D41;
        }
        QMenuBar { background-color: #181822; color: #DCDCF0; }
        QMenuBar::item:selected { background-color: #2D2D41; }
        QMenu { background-color: #1A1A24; color: #DCDCF0; border: 1px solid #2D2D41; }
        QMenu::item:selected { background-color: #2D2D41; }
        QToolBar { background-color: #181822; border: none; padding: 4px; }
        QComboBox, QPushButton, QToolButton {
            background-color: #2D2D41;
            color: #DCDCF0;
            border: 1px solid #3C3C5A;
            padding: 4px;
            margin: 0 4px;
        }
        QComboBox:hover, QPushButton:hover, QToolButton:hover { background-color: #3C3C5A; }
        QPushButton:disabled, QToolButton:disabled { background-color: #1A1A24; color: #555566; }
        QSplitter::handle { background-color: #2D2D41; }
        """
        self.setStyleSheet(qss)

    def new_file(self):
        self.editor.clear()
        self.current_file = None
        self.setWindowTitle("pyNASM Studio v3.0 (Professional Edition)")

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "Assembly Files (*.asm);;All Files (*)")
        if path:
            with open(path, "r", encoding="utf-8") as f:
                self.editor.setPlainText(f.read())
            self.current_file = path
            self.setWindowTitle(f"pyNASM Studio - {os.path.basename(path)}")

    def save_file(self):
        if not self.current_file:
            self.save_file_as()
        else:
            with open(self.current_file, "w", encoding="utf-8") as f:
                f.write(self.editor.toPlainText())
            self.terminal.append(f"[*] Saved to {self.current_file}")

    def save_file_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save File As", "", "Assembly Files (*.asm);;All Files (*)")
        if path:
            self.current_file = path
            self.save_file()
            self.setWindowTitle(f"pyNASM Studio - {os.path.basename(path)}")

    def open_linker_dialog(self):
        dialog = LinkerDialog(self.editor.toPlainText(), self)
        # Restore previous selection
        for d in self.linker_dlls:
            dialog.list_dlls.addItem(d)
        for e in self.linker_extra:
            dialog.list_extra.addItem(e)
            
        if dialog.exec():
            self.linker_dlls = dialog.get_dlls()
            self.linker_extra = dialog.get_extra_files()
            self.terminal.append(f"[*] Linker options updated: {len(self.linker_dlls)} DLLs, {len(self.linker_extra)} Extra Files.")

    def compile_code(self):
        self.terminal.clear()
        self.run_btn.setEnabled(False)
        self.last_executable = None
        
        code = self.editor.toPlainText()
        
        if self.current_file:
            self.save_file()
            out_dir = os.path.dirname(self.current_file)
            base_name = os.path.splitext(os.path.basename(self.current_file))[0]
        else:
            out_dir = None
            base_name = "output"
            
        t_text = self.target_combo.currentText()
        if "Console" in t_text:
            target = "console"
        elif "GUI" in t_text:
            target = "gui"
        elif "OBJ" in t_text:
            target = "obj"
        elif "BIOS" in t_text:
            target = "bios"
        elif "UEFI" in t_text:
            target = "uefi"
        else:
            target = "console"
            
        # Auto-detect entry point
        entry_point = "main"
        match = re.search(r'^\s*global\s+([a-zA-Z0-9_]+)', code, re.MULTILINE)
        if match:
            entry_point = match.group(1)
        elif target == "uefi":
            entry_point = "efi_main"

        config = {
            'target': target,
            'entry_point': entry_point,
            'dlls': self.linker_dlls.copy(),
            'extra_files': self.linker_extra.copy(),
            'output_dir': out_dir,
            'base_name': base_name
        }
        
        # Auto-append basic GoLink requirements if not present
        if target in ["console", "gui"]:
            if not any("kernel32" in d.lower() for d in config['dlls']):
                config['dlls'].append("kernel32.dll")
            if target == "gui":
                if not any("user32" in d.lower() for d in config['dlls']):
                    config['dlls'].append("user32.dll")
                if not any("gdi32" in d.lower() for d in config['dlls']):
                    config['dlls'].append("gdi32.dll")
                    
        self.worker = CompilerWorker(code, config)
        self.worker.log_signal.connect(self.append_terminal)
        self.worker.finished_signal.connect(self.on_compile_finished)
        self.worker.start()

    def on_compile_finished(self, success, exe_path):
        if success and exe_path and not exe_path.endswith('.obj'):
            self.last_executable = exe_path
            self.run_btn.setEnabled(True)

    def run_executable(self):
        if self.last_executable and os.path.exists(self.last_executable):
            self.terminal.append(f"[*] Launching {self.last_executable}...")
            try:
                if sys.platform == 'win32':
                    if self.last_executable.endswith('.bin') or self.last_executable.endswith('.efi'):
                        self.terminal.append(f"[-] Cannot natively run {self.last_executable}. Launch in an emulator like QEMU/Bochs.")
                    else:
                        os.startfile(self.last_executable)
                else:
                    subprocess.Popen([self.last_executable])
            except Exception as e:
                self.terminal.append(f"[-] Error launching: {e}")

    def append_terminal(self, msg, msg_type):
        color = "#DCDCF0"
        if msg_type == "error": color = "#FF5050"
        elif msg_type == "success": color = "#50C878"
        elif msg_type == "info": color = "#64A0FF"
        
        self.terminal.append(f'<span style="color: {color};">{msg}</span>')

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = pyNASMStudio()
    window.show()
    sys.exit(app.exec())
