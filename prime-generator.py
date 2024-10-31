import threading
import os
from math import isqrt
import struct
import time
from datetime import datetime


class PrimeGenerator:
    def __init__(self, known_primes_file, num_threads=8):
        print(f"\nIniciando gerador de primos otimizado em {datetime.now()}")
        self.num_threads = num_threads
        self.base = 340510170  # Base para geração de primos
        self.chunk_size = 16777216  # Tamanho do bloco a ser processado
        self.known_primes = self.load_known_primes(known_primes_file)
        print(f"Carregados {len(self.known_primes)} primos conhecidos")
        print(f"Último primo conhecido: {self.known_primes[-1]}")
        self.result_lock = threading.Lock()
        self.current_file_count = 0
        self.primes_per_file = 16_000_000  # Primos por arquivo
        self.total_primes_found = 0
        self.start_time = time.time()

    def load_known_primes(self, filename):
        """Carrega números primos conhecidos do arquivo em hexadecimal"""
        print(f"Carregando primos do arquivo: {filename}")
        try:
            with open(filename, 'r') as f:
                content = f.read().strip()
                primes = [int(x, 16) for x in content.split('E') if x]
                return primes
        except FileNotFoundError:
            print(f"Arquivo {filename} não encontrado. Usando lista básica de primos.")
            return [2, 3, 5, 7, 11, 13, 17, 19]#, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97, 101]

    def save_prime_pair(self, i, base_exp, outfile):
        """Salva par (i,j) em formato binário (16 bits cada)"""
        i_16 = i & 0xFFFF
        base_exp_16 = base_exp & 0xFFFF
        outfile.write(struct.pack('HH', i_16, base_exp_16))

    def update_stats(self, primes_found):
        """Atualiza estatísticas de progresso"""
        with self.result_lock:
            self.total_primes_found += primes_found
            elapsed = time.time() - self.start_time
            rate = self.total_primes_found / elapsed if elapsed > 0 else 0
            print(f"\rPrimos encontrados: {self.total_primes_found:,} | "
                  f"Taxa: {rate:.2f} primos/s | "
                  f"Tempo: {elapsed:.2f}s", end="")

    def process_prime(self, prime, base_exp, start, end, result_file, thread_id):
        """Processa um primo para encontrar novos primos"""
        local_count = 0

        print(f"\nThread {thread_id} iniciando para o primo {prime}: range {start} -> {end}")

        for i in range(start, end, 2):  # Verificando apenas ímpares
            n = (prime * i - 1) * ((pow(self.base, base_exp) - 1) & 0xFFFFFFFF) - 1
            if self.is_prime_witness(n):
                with self.result_lock:
                    self.save_prime_pair(i, base_exp, result_file)
                    local_count += 1
                    if local_count >= self.primes_per_file:
                        self.update_stats(local_count)
                        break  # Saia do loop se o número de primos locais atingir o limite
                if local_count % 1000 == 0:
                    self.update_stats(1000)

        # Atualize as estatísticas após a saída do loop
        self.update_stats(local_count)

    def generate_primes(self):
        """Função principal de geração de primos"""
        print(f"\nIniciando geração com {self.num_threads} threads")

        base_exp = 1
        while True:
            outfile_name = f"primes_chunk_{self.current_file_count}.bin"
            print(f"\nGerando arquivo: {outfile_name}")

            with open(outfile_name, 'wb') as outfile:
                threads = []
                print("\nIniciando threads...")
                for thread_index, prime in enumerate(self.known_primes):
                    # Define os limites de cada thread
                    start = 1 + (thread_index % self.num_threads) * self.chunk_size
                    end = start + self.chunk_size
                    thread = threading.Thread(
                        target=self.process_prime,
                        args=(prime, base_exp, start, end, outfile, thread_index)
                    )
                    threads.append(thread)
                    thread.start()

                for thread in threads:
                    thread.join()

                # Incrementa o contador de arquivos gerados
                self.current_file_count += 1

                # Avança o expoente após a geração de um arquivo
                base_exp += 1
                print(f"\nAvançando para expoente {base_exp}")

                # Condição de parada: continue até que não haja mais primos para processar
                if len(self.known_primes) == 0:
                    break

        total_time = time.time() - self.start_time
        print(f"\n\nGeração concluída em {total_time:.2f} segundos")
        print(f"Total de novos primos encontrados: {self.total_primes_found:,}")
        print(f"Arquivos gerados: {self.current_file_count}")

    def is_prime_witness(self, n, witnesses=None):
        """Teste de primalidade otimizado"""
        if n < 2: return False
        if n in self.known_primes: return True

        if witnesses is None:
            witnesses = [2, 3, 5, 7, 11, 13, 17, 19]# 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97, 101]

        def check_witness(a, n, d, r):
            x = pow(a, d, n)
            if x == 1 or x == n - 1:
                return True
            for _ in range(r - 1):
                x = (x * x) % n
                if x == n - 1:
                    return True
            return False

        r = 0
        d = n - 1
        while d % 2 == 0:
            r += 1
            d //= 2

        for a in witnesses:
            if a >= n: break
            if not check_witness(a, n, d, r):
                return False

        limit = isqrt(n)
        for p in self.known_primes:
            if p > limit:
                break
            if n % p == 0:
                return False
        return True


if __name__ == "__main__":
    print("Iniciando gerador de primos otimizado...")
    arquivo_primos = input("Arquivo de primos conhecido (padrão: primos_conhecidos.txt): ").strip()
    if not arquivo_primos:
        arquivo_primos = "primos_conhecidos.txt"

    # Inicia o gerador de primos
    prime_generator = PrimeGenerator(arquivo_primos)
    prime_generator.generate_primes()
