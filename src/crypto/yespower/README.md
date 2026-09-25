yespower 1.0
============

CPU-oriented proof-of-work hash by Alexander Peslyak (Solar Designer), based on
scrypt by Colin Percival: https://www.openwall.com/yespower/

CryptoLari's main and test networks check block proof of work with
yespower 1.0, N = 2048, r = 8, personalization "CryptoLari"
(see `GetBlockPoWHash` in `src/pow.cpp`). Regtest keeps SHA256d.

Files are unmodified from the yespower 1.0.0 release: `yespower-opt.c`,
`yespower-platform.c`, `yespower.h`, `sha256.c`, `sha256.h`, `sysendian.h`,
`insecure_memzero.h`. Both the optimized and the reference implementation of
that release reproduce its `TESTS-OK` vectors, and
`src/test/pow_tests.cpp` checks two of them.

License: 2-clause BSD, as stated at the top of each file.
