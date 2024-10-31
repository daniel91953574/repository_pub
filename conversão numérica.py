import sys
from math import log
sys.set_int_max_str_digits(0)
#corrigi da base 64K para 256
#pois não dá pra imprimir a tabuada do 64K, dá bilhões por operação
def NK(n:int):
    #Bc = "0123456789abcdefghijklmnopqrst"
    #intervalo 10240-<10496; o numero é usado apenas para selecionar unicode
    #resolvi fazer swap do char 10240 por 10496 (pois o outro é invisivel) 
    Bn = ''
    while n > 0:
        remainder = n%256
        if(remainder==0):
            remainder=256#swap chr
        Bn = chr(remainder+10240) + Bn
        n //= 256
    return Bn if Bn else '⤀'

def KN(Bn:str):
    #base30_digits = "0123456789abcdefghijklmnopqrst"
    n = 0; Bn
    for i,ch in enumerate(Bn):
        #desSwap
        oc = ord(ch)-10240
        if(oc==256):oc=0
        n = n*(256) + (oc) 
    return n

from mpmath import mp, floor

# Define a precisão desejada
mp.dps = 1000  # Define a precisão para 1000 dígitos

def convert_decimal_to_base30030(integer_part, float_part):
    integer_part = mp.mpf(integer_part)
    float_part = mp.mpf(float_part)

    # Conversão da parte inteira para base 30030
    integer_parts = []
    while integer_part > 0:
        part = integer_part % 30030
        integer_parts.append(int(part))
        integer_part = floor(integer_part / 30030)

    integer_converted = [decimal_to_base30(part) for part in integer_parts]

    # Conversão da parte decimal (float) para base 30030
    decimal_parts = []
    if float_part != 0:
        while float_part > 0 and len(decimal_parts) < 10:  # Limita a 10 dígitos
            float_part *= 30030
            integer_digit = floor(float_part)
            decimal_parts.append(integer_digit)
            float_part -= integer_digit

    decimal_converted = [decimal_to_base30(part) for part in decimal_parts]

    combined_integer = ','.join(integer_converted)
    combined_decimal = ','.join(decimal_converted)

    final_representation = f"{combined_integer}.{combined_decimal}" if decimal_converted else combined_integer

    return integer_parts, integer_converted, decimal_parts, decimal_converted, final_representation

def convert_decimal_to_base7420738134810(integer_part, float_part):
    integer_part = mp.mpf(integer_part)
    float_part = mp.mpf(float_part)

    # Conversão da parte inteira para base 7420738134810
    integer_parts = []
    while integer_part > 0:
        part = integer_part % 7420738134810
        integer_parts.append(int(part))
        integer_part = floor(integer_part / 7420738134810)

    integer_converted = [decimal_to_base30(part) for part in integer_parts]

    # Conversão da parte decimal (float) para base 7420738134810
    decimal_parts = []
    if float_part != 0:
        while float_part > 0 and len(decimal_parts) < 10:  # Limita a 10 dígitos
            float_part *= 7420738134810
            integer_digit = floor(float_part)
            decimal_parts.append(integer_digit)
            float_part -= integer_digit

    decimal_converted = [decimal_to_base30(part) for part in decimal_parts]

    combined_integer = ','.join(integer_converted)
    combined_decimal = ','.join(decimal_converted)

    final_representation = f"{combined_integer}.{combined_decimal}" if decimal_converted else combined_integer

    return integer_parts, integer_converted, decimal_parts, decimal_converted, final_representation

def decimal_to_base30(decimal_number):
    base30_digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if decimal_number < 0:
        return '-' + decimal_to_base30(-decimal_number)

    decimal_number = int(decimal_number)
    result = []
    if decimal_number == 0:
        return '0'

    while decimal_number > 0:
        remainder = decimal_number % 30
        result.append(base30_digits[remainder])
        decimal_number //= 30

    return ''.join(reversed(result))

# Exemplo de uso
integer_value = 0  # Parte inteira
float_value = 1/3  # Parte decimal

# Conversão para Base 30030
result_30030 = convert_decimal_to_base30030(integer_value, float_value)
print("Resultado na Base 30030:", result_30030)

# Conversão para Base 7420738134810
result_7420738134810 = convert_decimal_to_base7420738134810(integer_value, float_value)
print("Resultado na Base 7420738134810:", result_7420738134810)



"""
for i in range(0,256):
    for j in range(0,256):
        print(end=f"{NK(i*256+j)} ")
    print()
"""
"""
print(end="* \t")
for i in range(1,256):
    print(f"{NK(100)}[100]^{NK(i)}[{i}]: {NK(100**i)} \t")
print()
"""

"""
#tabela de soma
print(end="+|*")
for i in range(0,257):
    print(end=f"{i}|{hex(i).replace('0x','')} \t")
print()
for i in range(0,257):
    print(end=f"{i}|{hex(i).replace('0x','')}: ")
    for j in range(0,257):
        if(i>j):print(end=f"{NK(i+j)}{hex(i+j).replace('0x','')} \t")
        if(i==j):print(end=f"{NK(i+j)}{hex(i+j).replace('0x','')}|{NK(i*j)}{hex(i*j).replace('0x','')} \t")
        if(i<j):print(end=f"{NK(i*j)}{hex(i-j).replace('0x','')} \t")
    print()#x=input()#

print()
#tabela de exponenciação
print(end="^|√ \t")
for i in range(0,257):
    print(end=f"{i} \t")
print()
for i in range(0,257):
    print(end=f"{i}|{hex(i).replace('0x','')}: ")
    for j in range(1,257):
        print(end=f"{NK(i**j)}{hex(i**j).replace('0x','')}|{NK(i**(1/j))}{hex(i**(1/j)).replace('0x','')} \t")
    print()#x=input()#
for i in range(0,257):
    print(end=f"log{i}|{hex(i).replace('0x','')}: ")
    for j in range(0,257):
        print(end=f"{NK(log(j,i))}{hex(log(j,i).replace('0x',''))} \t")
"""

#
