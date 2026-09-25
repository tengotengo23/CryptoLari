# ვორქშოპი „ააწყვე საკუთარი კრიპტოვალუტა“: მონაწილის ფურცელი

⚠️ დღეს ვმუშაობთ CryptoLari-ს **testnet**-ზე. აქ მონეტებს ფასი არ აქვს. ეს
სასწავლო ღონისძიებაა და არა საინვესტიციო რჩევა.

ინსტრუქტორის IP: `______________` · ექსპლორერი: `http://______________:8080/`

---

## 0. მომზადება (Ubuntu ან Windows-ზე WSL2 + Ubuntu)

```bash
git clone https://github.com/tengotengo23/CryptoLari.git
cd CryptoLari
git checkout claude/hopeful-mayer-l05sqi
contrib/cryptolari/quickstart.sh --test --addnode <ინსტრუქტორის IP>
```

პირველ ჯერზე აწყობას 10–30 წუთი სჭირდება. ბოლოს ნახავ:
`CryptoLari is running.` და შენს მისამართს (`T…`).

მოხერხებულობისთვის შექმენი მოკლე ბრძანება:

```bash
alias cl="$PWD/src/cryptolari-cli -testnet"
```

---

## ლაბ 1: კვანძი

```bash
cl getconnectioncount        # რამდენ კვანძს უკავშირდები (≥ 1 უნდა იყოს)
cl getblockcount             # რამდენი ბლოკი გაქვს (ინსტრუქტორის რიცხვს უნდა დაეწიოს)
cl getpeerinfo               # ვის უკავშირდები
```

**კითხვა:** რატომ ამოწმებს შენი კომპიუტერი ყველა ბლოკს თავიდან, თუ ინსტრუქტორმა
ისინი უკვე შეამოწმა?

---

## ლაბ 2: საფულე და ტრანზაქცია

```bash
cl getaccountaddress ""      # შენი მისამართი: ჩაწერე ჯგუფის ჩატში
cl getbalance                # ბალანსი (დადასტურებული)
cl getunconfirmedbalance     # ჯერ დაუდასტურებელი
```

მეზობელს გაუგზავნე 1 CLARI:

```bash
cl sendtoaddress <მეზობლის მისამართი> 1
```

ბრძანება დაგიბრუნებს ტრანზაქციის ID-ს (txid). ჩასვი ის ექსპლორერის საძიებოში და
ნახე, როგორ გადადის ტრანზაქცია „რიგიდან“ ბლოკში.

```bash
cl gettransaction <txid>     # confirmations: რამდენი ბლოკი დაემატა მას შემდეგ
```

**კითხვა:** რატომ ვერ „გააუქმებ“ უკვე გაგზავნილ ტრანზაქციას?

---

## ლაბ 3: მაინინგი

```bash
contrib/cryptolari/mine.sh --test 2      # 2 = CPU ბირთვი; გაჩერება: Ctrl+C
```

სხვა ფანჯარაში:

```bash
cl getmininginfo             # difficulty: როგორ იზრდება, როცა ყველა მაინინგს აკეთებს
cl getwalletinfo             # immature_balance: მოპოვებული, ჯერ ხარჯვადი არ არის
```

**კითხვა:** რატომ იზრდება სირთულე, როცა მეტი ადამიანი მაინინგს აკეთებს?

---

## ლაბ 4: როგორ იქმნება კრიპტოვალუტა

გახსენი `src/chainparams.cpp` და იპოვე:

| რას ვეძებთ | რა ჰქვია კოდში |
|---|---|
| პირველი ბლოკის ტექსტი | `CreateCryptoLariGenesisBlock` → `pszTimestamp` |
| ჯილდო ბლოკზე | `nInitialSubsidy` |
| ნახევრების ინტერვალი | `nSubsidyHalvingInterval` |
| ბლოკის დრო | `nPowTargetSpacing` |
| მისამართის პირველი ასო | `base58Prefixes[PUBKEY_ADDRESS]` |

**ბონუსი:** მოიპოვე შენი genesis ბლოკი:

```bash
contrib/cryptolari/mine-genesis.py --time 2026-10-01 --message "<შენი სახელი>'s coin"
```

---

## დაიმახსოვრე

* 🔑 საიდუმლო გასაღები ან ფრაზა არავის გაუზიარო.
* 🚩 „გარანტირებული მოგება“ თაღლითობის ნიშანია.
* 🧪 ყველაფერი ჯერ testnet-ზე სცადე.

სახლში გაგრძელება: `README.md` და `CRYPTOLARI_PLAN.md` ამავე რეპოზიტორიაში.
