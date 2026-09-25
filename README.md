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
0.15.99 with its own genesis block, CPU-oriented yespower proof of work,
2-minute blocks, per-block LWMA difficulty adjustment and its own address formats. It is not affiliated with the National
Bank of Georgia and is not pegged to the Georgian lari.*

პარამეტრები
-----------

| | Mainnet | Testnet |
|---|---|---|
| ტიკერი | CLARI | CLARI (ღირებულების გარეშე) |
| Proof-of-Work | yespower 1.0 (N=2048, r=8, „CryptoLari“): მაინინგი ჩვეულებრივ CPU-ზე | იგივე |
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

Genesis ბლოკი (mainnet): `5353972a349d18c895f2ceba17523c6b12ca0b497b02f65d46adb4efd98533cf`
(ბლოკის ID კვლავ SHA-256d-ით ითვლება, proof-of-work კი yespower-ით მოწმდება.)

Regtest (ლოკალური სატესტო ქსელი ავტომატური ტესტებისთვის) განზრახ იყენებს Bitcoin-ის
regtest-ის წესებს (მათ შორის SHA-256d-ს), რომ upstream-ის ტესტები უცვლელად მუშაობდეს.

სწრაფი დაწყება: ერთი ბრძანება
-----------------------------

Ubuntu/Debian-ზე ყველაფერს ერთი სკრიპტი აკეთებს: აყენებს ბიბლიოთეკებს, აწყობს
პროგრამას, უშვებს კვანძს (ნაგულისხმევად testnet-ზე) და გიჩვენებს შენს მისამართს:

```bash
contrib/cryptolari/quickstart.sh            # testnet; mainnet-ისთვის: --main
contrib/cryptolari/mine.sh --test 2         # მაინინგი 2 CPU ბირთვით (Ctrl+C აჩერებს)
contrib/cryptolari/explorer/explorer.py --network test   # ექსპლორერი: http://127.0.0.1:8080/
```

| რა | სად |
|---|---|
| ბლოკ-ექსპლორერი + ონკანი (ვებ-გვერდი) | [`contrib/cryptolari/explorer/`](contrib/cryptolari/explorer/explorer.py) |
| სერვერზე გაშვება (systemd, HTTPS) | [`contrib/cryptolari/deploy/DEPLOY.md`](contrib/cryptolari/deploy/DEPLOY.md) |
| ვორქშოპის კომპლექტი (გასაყიდად) | [`workshop/`](workshop/README.md) |
| შემოსავლის გეგმა | [`SHEMOSAVLIS_GEGMA.md`](SHEMOSAVLIS_GEGMA.md) |

აწყობა ხელით (Ubuntu / Debian)
------------------------------

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

პროგრამები: `src/cryptolarid` (კვანძი), `src/cryptolari-cli` (ბრძანებები),
`src/cryptolari-tx` (ტრანზაქციების ხელსაწყო).

გაშვება და მაინინგი
-------------------

```bash
src/cryptolarid -daemon                         # კვანძის გაშვება (mainnet)
src/cryptolari-cli getblockchaininfo            # ქსელის მდგომარეობა
ADDR=$(src/cryptolari-cli getnewaddress)        # ახალი მისამართი, იწყება G-თი
src/cryptolari-cli generatetoaddress 10 $ADDR 100000000   # 10 ბლოკის მოპოვება CPU-თი
src/cryptolari-cli getbalance                   # ჯილდო ხელმისაწვდომია 100 ბლოკის შემდეგ
src/cryptolari-cli sendtoaddress <მისამართი> 1.5
src/cryptolari-cli stop
```

* სხვა კვანძთან დაკავშირება: `src/cryptolarid -daemon -addnode=<IP>:9955`
  (საჯარო seed კვანძები ჯერ არ არსებობს).
* სატესტო ქსელი: ყველა ბრძანებას დაამატე `-testnet`.
* პირველი 90 ბლოკი მინიმალური სირთულითაა (ერთ CPU ბირთვზე ~4 წამი ბლოკზე), შემდეგ
  LWMA სირთულეს ყოველ ბლოკზე ქსელის რეალურ სიმძლავრეს უსადაგებს.

რა შეიცვალა Bitcoin Core-თან შედარებით
--------------------------------------

* ახალი genesis ბლოკი, network magic, პორტები, მისამართების პრეფიქსები და bech32 HRP,
  ამიტომ CryptoLari-სა და Bitcoin-ის მისამართები ერთმანეთში არ აგერევა.
* yespower proof-of-work (`src/crypto/yespower`, `GetBlockPoWHash` `src/pow.cpp`-ში):
  Bitcoin-ის ASIC-ებით ამ ქსელის მოპოვება ან შეტევა შეუძლებელია.
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
* **51%-იანი შეტევის რისკი შემცირდა, მაგრამ არ გაქრა.** yespower-ის გამო Bitcoin-ის
  ASIC-ები აქ უსარგებლოა, მაგრამ პატარა ქსელს მაინც შეუტევს ის, ვინც ბევრ CPU-ს
  (ღრუბლოვან სერვერებს) იქირავებს. რაც მეტი ადამიანი მოიპოვებს, მით უფრო რთულია.
* გაშვების დღეს genesis ხელახლა უნდა მოიპოვო ახალი თარიღით
  (`contrib/cryptolari/mine-genesis.py`), რომ ყველამ დაინახოს: წინასწარ არავის
  მოუპოვებია.

ლიცენზია
--------

CryptoLari ვრცელდება MIT ლიცენზიით, იხ. [COPYING](COPYING). კოდის უდიდესი ნაწილი
Bitcoin Core-ის დეველოპერებს ეკუთვნის (https://github.com/bitcoin/bitcoin).
