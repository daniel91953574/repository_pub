import re
from PyQt6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from PyQt6.QtCore import QRegularExpression
from core.database import INSTRUCTIONS, REGISTERS, DIRECTIVES, ASMX_MACROS

class ASMSyntaxHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.highlighting_rules = []

        # Colors (Dark Theme)
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor(100, 180, 255))
        
        register_format = QTextCharFormat()
        register_format.setForeground(QColor(255, 160, 60))
        
        directive_format = QTextCharFormat()
        directive_format.setForeground(QColor(160, 120, 255))
        
        asmx_format = QTextCharFormat()
        asmx_format.setForeground(QColor(255, 120, 120))
        asmx_format.setFontWeight(QFont.Weight.Bold)

        label_format = QTextCharFormat()
        label_format.setForeground(QColor(255, 200, 80))

        number_format = QTextCharFormat()
        number_format.setForeground(QColor(120, 220, 140))

        string_format = QTextCharFormat()
        string_format.setForeground(QColor(200, 130, 200))

        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor(80, 140, 80))

        # Build regex patterns
        # 1. Instructions
        instructions_pattern = r'\b(' + '|'.join(INSTRUCTIONS.keys()) + r')\b'
        self.highlighting_rules.append((QRegularExpression(instructions_pattern), keyword_format))

        # 2. Registers
        all_regs = []
        for regs in REGISTERS.values():
            all_regs.extend(regs)
        registers_pattern = r'\b(' + '|'.join(all_regs) + r')\b'
        self.highlighting_rules.append((QRegularExpression(registers_pattern), register_format))

        # 3. Directives
        dirs = [d for d in DIRECTIVES] + [f"%{d}" for d in DIRECTIVES]
        directives_pattern = r'\b(' + '|'.join(dirs).replace('%', r'\%') + r')\b'
        self.highlighting_rules.append((QRegularExpression(directives_pattern), directive_format))

        # 4. ASMX Macros
        asmx_pattern = r'(' + '|'.join(ASMX_MACROS.keys()).replace('@', r'\@') + r')\b'
        self.highlighting_rules.append((QRegularExpression(asmx_pattern), asmx_format))

        # 5. Labels
        self.highlighting_rules.append((QRegularExpression(r'^[A-Za-z_][A-Za-z0-9_]*:'), label_format))
        self.highlighting_rules.append((QRegularExpression(r'^\.[A-Za-z_][A-Za-z0-9_]*:'), label_format))

        # 6. Numbers (Hex, Dec, Bin)
        self.highlighting_rules.append((QRegularExpression(r'\b0[xX][0-9a-fA-F]+\b'), number_format))
        self.highlighting_rules.append((QRegularExpression(r'\b[0-9]+[hHbB]?\b'), number_format))

        # 7. Strings
        self.highlighting_rules.append((QRegularExpression(r'".*"'), string_format))
        self.highlighting_rules.append((QRegularExpression(r"'.*'"), string_format))

        # 8. Comments
        self.comment_format = comment_format
        self.comment_regex = QRegularExpression(r';.*')

    def highlightBlock(self, text):
        # Apply standard rules
        for pattern, fmt in self.highlighting_rules:
            match_iterator = pattern.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

        # Apply comment rule (overrides anything else it overlaps)
        match_iterator = self.comment_regex.globalMatch(text)
        while match_iterator.hasNext():
            match = match_iterator.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), self.comment_format)
