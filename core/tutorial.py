# Tutorial Data for Guided Mode

TUTORIAL_STEPS = [
    {
        "title": "Bem-vindo ao pyNASM Studio! 🚀",
        "text": (
            "Este é o seu guia interativo de Assembly x64.\n\n"
            "Nós passaremos por conceitos essenciais de programação de baixo nível para Windows. "
            "Você aprenderá como escrever, compilar e executar o código nativamente.\n\n"
            "Em cada passo, você poderá clicar no botão [Inserir Exemplo] para jogar o código "
            "diretamente no editor, de forma a compilar e testar a etapa!"
        ),
        "code": "; --- Início do Tutorial ---\n; Clique em Avançar para a Lição 1!"
    },
    {
        "title": "Lição 1: Estrutura Básica de um Módulo 64-bit",
        "text": (
            "Todo programa em Assembly x64 para NASM deve informar ao compilador a arquitetura "
            "utilizada e como os endereços de memória serão tratados.\n\n"
            "- 'bits 64' diz ao NASM que estamos no modo de 64-bits.\n"
            "- 'default rel' ativa o endereçamento relativo ao RIP, exigência obrigatória do Windows x64.\n"
            "- 'section .text' declara o início da seção de código executável.\n"
            "- 'global main' exporta a sua função principal para o Linker (GoLink) enxergar."
        ),
        "code": "bits 64\ndefault rel\n\nsection .text\nglobal main\n\nmain:\n    ; Seu código começará aqui!\n    ret"
    },
    {
        "title": "Lição 2: Chamando Funções do Windows (API C)",
        "text": (
            "No Windows, não chamamos interrupções de kernel diretamente como no Linux (syscalls). "
            "Ao invés disso, chamamos funções da API exportadas por DLLs como kernel32.dll.\n\n"
            "Nesta lição, declaramos 'extern ExitProcess'. Essa função encerra o programa de forma limpa. "
            "Ela espera 1 parâmetro (o código de saída). No x64, o primeiro parâmetro inteiro é passado no "
            "registrador RCX/ECX.\n\n"
            "Aqui usamos 'xor ecx, ecx', que zera o registrador ECX (equivalente a passar 0), e depois 'call ExitProcess'."
        ),
        "code": "bits 64\ndefault rel\n\nsection .text\nglobal main\nextern ExitProcess\n\nmain:\n    xor ecx, ecx    ; rcx = 0 (exit code)\n    call ExitProcess"
    },
    {
        "title": "Lição 3: O Shadow Space (ABI x64)",
        "text": (
            "Atenção! Diferente da arquitetura de 32 bits (onde parâmetros iam na pilha), no Windows x64 "
            "nós precisamos pré-alocar 32 bytes na pilha logo de cara antes de chamar qualquer função externa! "
            "Isso é conhecido como **Shadow Space**.\n\n"
            "Além disso, a pilha precisa estar alinhada em blocos de 16-bytes antes do `call`. "
            "O prólogo padrão (`push rbp`, `mov rbp, rsp`, `sub rsp, 32`) resolve tudo isso de uma vez."
        ),
        "code": "bits 64\ndefault rel\n\nsection .text\nglobal main\nextern ExitProcess\n\nmain:\n    push rbp\n    mov rbp, rsp\n    sub rsp, 32     ; Aloca 32 bytes de Shadow Space\n\n    xor ecx, ecx\n    call ExitProcess"
    },
    {
        "title": "Lição 4: Olá, Console! (printf)",
        "text": (
            "Para exibir um texto na tela do Console, usaremos o `printf` da biblioteca `msvcrt.dll`.\n\n"
            "Lembrando:\n"
            "- 1º parâmetro vai para `RCX` (neste caso, o endereço da string).\n"
            "Vamos declarar a string na seção `.data` e carregar o seu endereço com a instrução `lea` (Load Effective Address)."
        ),
        "code": "bits 64\ndefault rel\n\nsection .data\n    msg db 'Ola, Assembly x64!', 10, 0\n\nsection .text\nglobal main\nextern printf\nextern ExitProcess\n\nmain:\n    push rbp\n    mov rbp, rsp\n    sub rsp, 32\n\n    lea rcx, [rel msg]  ; 1º arg: ponteiro para string\n    call printf\n\n    xor ecx, ecx\n    call ExitProcess"
    },
    {
        "title": "Lição 5: Interface Gráfica (MessageBox)",
        "text": (
            "Podemos fazer aplicações com janelas importando a `user32.dll`! A função `MessageBoxA` recebe 4 parâmetros. "
            "Na convenção x64, eles vão para: `RCX`, `RDX`, `R8`, `R9`.\n\n"
            "Parâmetros do MessageBox:\n"
            "1. RCX: Handle (0 = janela nula)\n"
            "2. RDX: Texto da Mensagem\n"
            "3. R8: Título da Janela\n"
            "4. R9: Estilo (0 = MB_OK)"
        ),
        "code": "bits 64\ndefault rel\n\nsection .data\n    msg db 'Injetado com sucesso no pyNASM Studio!', 0\n    titulo db 'Alerta Gráfico', 0\n\nsection .text\nglobal main\nextern MessageBoxA\nextern ExitProcess\n\nmain:\n    push rbp\n    mov rbp, rsp\n    sub rsp, 32\n\n    xor ecx, ecx           ; RCX = 0 (Janela pai nula)\n    lea rdx, [rel msg]     ; RDX = Texto\n    lea r8, [rel titulo]   ; R8  = Titulo\n    xor r9d, r9d           ; R9  = 0 (MB_OK)\n    call MessageBoxA\n\n    xor ecx, ecx\n    call ExitProcess"
    },
    {
        "title": "Lição 6: Compilando o seu Código",
        "text": (
            "Parabéns! O seu código está pronto.\n\n"
            "Agora veja a barra superior da ferramenta.\n"
            "1. Escolha o Target: Se você tem um 'printf', use [Win64 Console EXE]. Se tem 'MessageBoxA', use [Win64 GUI EXE].\n"
            "2. Pressione o botão verde [▶ Compile]. Ele montará usando o NASM e linkará o GoLink automaticamente.\n"
            "3. Se houver erro, olhe a aba do TERMINAL embaixo para corrigir.\n"
            "4. Se funcionar, clique no botão azul [🚀 Run] para ver o programa ganhando vida!"
        ),
        "code": "; O Tutorial terminou!\n; Sinta-se a vontade para explorar o Menu de 'Registrador~Memoria'\n; e os Operadores para descobrir novas instruções!"
    }
]
