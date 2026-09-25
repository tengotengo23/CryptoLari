CryptoLari (LARI)
=================

CryptoLari არის Bitcoin Core-ის (v0.16) ფორკი საკუთარი ქსელით, გენეზის ბლოკით და
ეკონომიკით. ყოველი ბლოკის ჯილდოს 5% ავტომატურად მიდის დეველოპერის მისამართზე
(dev fee) და ამას კონსენსუსის წესი იცავს: ბლოკი, რომელიც dev fee-ს არ იხდის,
ქსელისთვის არავალიდურია.

CryptoLari is a fork of Bitcoin Core (v0.16) with its own network, genesis block
and economics. 5% of every block subsidy is paid to the developer (dev fee).
This is a consensus rule: blocks that do not pay the dev fee are invalid.

პარამეტრები / Parameters
------------------------

| | mainnet | testnet | regtest |
|---|---|---|---|
| ტიკერი / unit | LARI | LARI | LARI |
| მაქს. მარაგი / max supply | 84,000,000 | 84,000,000 | (უცვლელი / unchanged) |
| ბლოკის ჯილდო / block subsidy | 100 LARI | 100 LARI | 50 |
| halving | ყოველ 420,000 ბლოკში (~2 წელი) | 420,000 | 150 |
| ბლოკის დრო / block time | 2.5 წთ | 2.5 წთ | - |
| სირთულის გადათვლა / retarget | 2016 ბლოკი (3.5 დღე) | 2016 | - |
| dev fee | 5% subsidy-ის, ბლოკი 1-დან | 5% | გამორთული, `-devfeeheight=<n>` |
| P2P პორტი / port | 9555 | 19555 | 18444 |
| RPC პორტი / port | 9554 | 19554 | 18443 |
| მისამართები / addresses | `G...` (P2PKH), `T...` (P2SH), `lari1...` (bech32) | `t...`, `u...`, `tlari1...` | bitcoin regtest-ის მსგავსი |
| message start | `c1 a2 d5 e7` | `c2 b3 a4 e1` | `fa bf b5 da` |

* dev fee ითვლება მხოლოდ subsidy-დან (ტრანზაქციის საკომისიოები მთლიანად მაინერს
  რჩება) და halving-თან ერთად მცირდება: 5 LARI ბლოკზე, მერე 2.5, და ა.შ.
  სულ dev fee-დან: 5% × 84M = **4,200,000 LARI**.
* BIP16/34/65/66, CSV და SegWit აქტიურია თავიდანვე.
* მონაცემების დირექტორია: `~/.cryptolari` (Windows: `%APPDATA%\CryptoLari`),
  კონფიგურაცია: `cryptolari.conf`. პროგრამები: `cryptolarid` (node),
  `cryptolari-cli`, `cryptolari-tx`, `cryptolari-qt` (GUI).

The dev fee is taken from the subsidy only (miners keep all transaction fees)
and halves together with it. Total dev fee over the chain's lifetime:
5% × 84M = 4.2M LARI.

საკუთარი მისამართის ჩასმა (აუცილებელია mainnet-მდე!) / Setting your dev fee address
-----------------------------------------------------------------------------------

ახლა dev fee მიდის placeholder სკრიპტზე (`76a914 00..00 88ac`), რომლის
დახარჯვა არავის შეუძლია. ამიტომ `cryptolarid` mainnet-ზე **არ ჩაირთვება**,
სანამ მას არ შეცვლი.

1. ააწყე პროექტი (იხ. ქვემოთ) და შექმენი მისამართი testnet-ზე:

       src/cryptolarid -testnet -daemon
       src/cryptolari-cli -testnet getnewaddress "devfee"      # -> t...

2. **შეინახე გასაღები უსაფრთხოდ**:

       src/cryptolari-cli -testnet backupwallet /უსაფრთხო/ადგილი/devfee-wallet.dat

   ვინც ამ გასაღებს ფლობს, ის ფლობს dev fee-ს. თუ დაკარგე, dev fee სამუდამოდ
   დაიკარგება. შეინახე რამდენიმე ასლი offline (USB, ქაღალდზე `dumpprivkey`).
   mainnet-ზე ამ ფულის დასახარჯად `devfee-wallet.dat` ჩააგდე
   `~/.cryptolari/wallet.dat`-ად, ან გამოიყენე `importprivkey`.

3. ჩასვი მისამართი კოდში ერთი ბრძანებით (იგივე გასაღები მუშაობს mainnet-ზეც
   და testnet-ზეც):

       contrib/cryptolari/set_devfee_address.py <მისამართი>
       make -j$(nproc) && make check

   სურვილისამებრ `src/chainparams.cpp`-ში შეცვალე `DEV_FEE_PERCENT` (ახლა 5).

4. ეს ცვლილება ქსელის გაშვების **შემდეგ** აღარ შეიცვლება hard fork-ის გარეშე.

The mainnet dev fee currently goes to an unspendable placeholder, and `cryptolarid`
refuses to start on mainnet until you run
`contrib/cryptolari/set_devfee_address.py <address>` with an address you control
(create it with `getnewaddress` on testnet and back up the wallet!).

მაინინგი / Mining
-----------------

* `generatetoaddress` / შიდა მაინერი dev fee-ს თავისით ამატებს. CPU-თი მაინინგი:

      src/cryptolari-cli generatetoaddress 1 <შენი მისამართი> 100000000

* პულებისთვის `getblocktemplate` აბრუნებს ველს
  `"devfee": {"script": "<hex>", "amount": <satoshi>}`. `coinbasevalue` უკვე
  dev fee-ს გარეშეა, და პულის coinbase-ს უნდა ჰქონდეს დამატებითი output ამ
  script-ზე მინიმუმ `amount` ოდენობით. სტანდარტულ Bitcoin პულის პროგრამას ეს
  ცვლილება სჭირდება.

Pools must add an extra coinbase output paying `devfee.amount` to `devfee.script`
as returned by `getblocktemplate`; `coinbasevalue` already excludes the dev fee.

აწყობა / Building (Ubuntu 24.04)
--------------------------------

    sudo apt-get install build-essential libtool autotools-dev automake pkg-config \
        libssl-dev libevent-dev bsdmainutils libdb++-dev \
        libboost-system-dev libboost-filesystem-dev libboost-chrono-dev \
        libboost-program-options-dev libboost-test-dev libboost-thread-dev
    ./autogen.sh
    ./configure --with-incompatible-bdb
    make -j$(nproc)
    make check                                  # unit + cryptolari-tx tests
    test/functional/test_runner.py              # functional tests

ქსელის გაშვება / Launching the network
--------------------------------------

1. ჩასვი საკუთარი dev fee მისამართი (იხ. ზემოთ).
2. გაუშვი რამდენიმე საჯარო node (VPS) და ჩაამატე `vSeeds`/`vFixedSeeds`
   `src/chainparams.cpp`-ში (ახლა ცარიელია, ამიტომ node-ები ერთმანეთს
   `-addnode=<ip>:9555`-ით უნდა მიუერთო).
3. დაიწყე მაინინგი. პირველი ბლოკების მოპოვება CPU-თიც შეიძლება
   (საწყისი სირთულე დაბალია, `powLimit = 00000fff...`).
4. dev fee საჯაროდ გაამჟღავნე (README, საიტი), რათა მომხმარებლებმა და
   მაინერებმა იცოდნენ.
