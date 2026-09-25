CryptoLari
==========

CryptoLari (CLARI) არის ექსპერიმენტული peer-to-peer ციფრული ვალუტა, რომელიც
Bitcoin Core-ის კოდზეა აგებული. მას აქვს საკუთარი ბლოკჩეინი, საკუთარი მისამართები
და საკუთარი ქსელი: Bitcoin-ის ქსელთან არ არის დაკავშირებული.

> **CryptoLari არ არის საქართველოს ეროვნული ვალუტა (ლარი, GEL)** და არანაირი კავშირი
> არ აქვს საქართველოს ეროვნულ ბანკთან. მისი ფასი ₾-ზე მიბმული არ არის.

📄 **რა არის ეს პროექტი, როგორ შეიძლება ფულის გამომუშავება და ნაბიჯ-ნაბიჯ გეგმა:
[CRYPTOLARI_PLAN.md](CRYPTOLARI_PLAN.md)**

*English: CryptoLari is an experimental cryptocurrency forked from Bitcoin Core
0.15.99 with its own genesis block, 2-minute blocks, per-block LWMA difficulty
adjustment and its own address formats. It is not affiliated with the National
Bank of Georgia and is not pegged to the Georgian lari.*

პარამეტრები
-----------

| | Mainnet | Testnet |
|---|---|---|
| ტიკერი | CLARI | CLARI (ღირებულების გარეშე) |
| Proof-of-Work | SHA-256d | SHA-256d |
| ბლოკის დრო | 2 წუთი | 2 წუთი |
| ჯილდო ბლოკზე | 10 CLARI, ნახევრდება ყოველ 1,050,000 ბლოკში (~4 წელი) | იგივე |
| მაქსიმალური მარაგი | ≈ 21,000,000 CLARI (20,999,999.8635) | იგივე |
| სირთულის გადათვლა | ყოველ ბლოკზე, LWMA-1 (N = 90 ბლოკი) | იგივე |
| Coinbase maturity | 100 ბლოკი (~3.3 საათი) | იგივე |
| მისამართები | `G…` (P2PKH), `S…` (P2SH), `lari1…` (SegWit) | `T…`, `U…`, `tlari1…` |
| P2P / RPC პორტი | 9955 / 9954 | 19955 / 19954 |
| Premine | არ არის (genesis-ის ჯილდო OP_RETURN-ზეა, დახარჯვა შეუძლებელია) | არ არის |
| Soft forks | P2SH, BIP34/65/66, CSV, SegWit აქტიურია პირველივე ბლოკიდან | იგივე |
| მონაცემების საქაღალდე | `~/.cryptolari`, კონფიგი `cryptolari.conf` | `~/.cryptolari/testnet` |

Genesis ბლოკი (mainnet): `00000812596cb6b8240d2576f125d8a3434f6786403ffda50b4fdc74416af73d`

Regtest (ლოკალური სატესტო ქსელი ავტომატური ტესტებისთვის) განზრახ იყენებს Bitcoin-ის
regtest-ის წესებს, რომ upstream-ის ტესტები უცვლელად მუშაობდეს.

აწყობა (Ubuntu / Debian)
-----------------------

```bash
sudo apt install build-essential libtool autotools-dev automake pkg-config bsdmainutils python3 \
    libssl-dev libevent-dev libdb5.3++-dev \
    libboost-system-dev libboost-filesystem-dev libboost-chrono-dev \
    libboost-program-options-dev libboost-test-dev libboost-thread-dev

./autogen.sh
./configure --with-incompatible-bdb --without-gui
make -j4
make check        # ტესტები
```

პროგრამები ჯერ ძველი სახელებით იქმნება: `src/bitcoind` (კვანძი), `src/bitcoin-cli`
(ბრძანებები), `src/bitcoin-tx`.

გაშვება და მაინინგი
-------------------

```bash
src/bitcoind -daemon                         # კვანძის გაშვება (mainnet)
src/bitcoin-cli getblockchaininfo            # ქსელის მდგომარეობა
ADDR=$(src/bitcoin-cli getnewaddress)        # ახალი მისამართი, იწყება G-თი
src/bitcoin-cli generatetoaddress 10 $ADDR 100000000   # 10 ბლოკის მოპოვება CPU-თი
src/bitcoin-cli getbalance                   # ჯილდო ხელმისაწვდომია 100 ბლოკის შემდეგ
src/bitcoin-cli sendtoaddress <მისამართი> 1.5
src/bitcoin-cli stop
```

* სხვა კვანძთან დაკავშირება: `src/bitcoind -daemon -addnode=<IP>:9955`
  (საჯარო seed კვანძები ჯერ არ არსებობს).
* სატესტო ქსელი: ყველა ბრძანებას დაამატე `-testnet`.
* პირველი 90 ბლოკი მინიმალური სირთულითაა, შემდეგ LWMA სირთულეს ყოველ ბლოკზე
  ქსელის რეალურ სიმძლავრეს უსადაგებს.

რა შეიცვალა Bitcoin Core-თან შედარებით
--------------------------------------

* ახალი genesis ბლოკი, network magic, პორტები, მისამართების პრეფიქსები და bech32 HRP,
  ამიტომ CryptoLari-სა და Bitcoin-ის მისამართები ერთმანეთში არ აგერევა.
* 2-წუთიანი ბლოკები, 10 CLARI ჯილდო და ≈21M მარაგი (`consensus.nInitialSubsidy`).
* LWMA-1 სირთულის ალგორითმი (`src/pow.cpp`), მომავლის timestamp-ის ლიმიტი 10 წუთი
  და peer-ების საათის კორექციის ლიმიტი 5 წუთი.
* გასწორებულია კრიტიკული ხარვეზი CVE-2018-17144 (ორმაგი input-ის შემოწმება ბლოკში).
* კოდი აეწყობა თანამედროვე GCC 13 / Boost 1.83-ით.
* `contrib/cryptolari/mine-genesis.py`: ახალი genesis ბლოკის მოპოვება გაშვების დღეს.

⚠️ მნიშვნელოვანი შეზღუდვები
--------------------------

* **კოდი 2017 წლისაა** (Bitcoin Core 0.15.99). მას შემდეგ Bitcoin Core-ში ბევრი
  უსაფრთხოების შესწორება შევიდა. სანამ ქსელში რეალური ფული აღმოჩნდება, ეს
  პარამეტრები Bitcoin Core-ის ახალ ვერსიაზე უნდა გადავიდეს.
* **SHA-256 = 51%-იანი შეტევის რისკი.** ნებისმიერს, ვისაც Bitcoin-ის ASIC აქვს ან
  სიმძლავრეს იქირავებს, პატარა ქსელის ისტორიის გადაწერა შეუძლია.
* გაშვების დღეს genesis ხელახლა უნდა მოიპოვო ახალი თარიღით
  (`contrib/cryptolari/mine-genesis.py`), რომ ყველამ დაინახოს: წინასწარ არავის
  მოუპოვებია.

ლიცენზია
--------

CryptoLari ვრცელდება MIT ლიცენზიით, იხ. [COPYING](COPYING). კოდის უდიდესი ნაწილი
Bitcoin Core-ის დეველოპერებს ეკუთვნის (https://github.com/bitcoin/bitcoin).
