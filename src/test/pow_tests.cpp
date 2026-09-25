// Copyright (c) 2015 The Bitcoin Core developers
// Distributed under the MIT/X11 software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#include <chain.h>
#include <chainparams.h>
#include <crypto/yespower/yespower.h>
#include <pow.h>
#include <random.h>
#include <util.h>
#include <utilstrencodings.h>
#include <test/test_bitcoin.h>

#include <boost/test/unit_test.hpp>

BOOST_FIXTURE_TEST_SUITE(pow_tests, BasicTestingSetup)

/* Bitcoin's original 2016-block retarget (still used by regtest), tested against real Bitcoin blocks. */
static Consensus::Params BitcoinRetargetParams()
{
    Consensus::Params params = CreateChainParams(CBaseChainParams::MAIN)->GetConsensus();
    params.powLimit = uint256S("00000000ffffffffffffffffffffffffffffffffffffffffffffffffffffffff");
    params.nPowTargetSpacing = 10 * 60;
    params.nPowTargetTimespan = 14 * 24 * 60 * 60;
    params.nLwmaAveragingWindow = 0;
    return params;
}

/* Test calculation of next difficulty target with no constraints applying */
BOOST_AUTO_TEST_CASE(get_next_work)
{
    const Consensus::Params params = BitcoinRetargetParams();
    int64_t nLastRetargetTime = 1261130161; // Block #30240
    CBlockIndex pindexLast;
    pindexLast.nHeight = 32255;
    pindexLast.nTime = 1262152739;  // Block #32255
    pindexLast.nBits = 0x1d00ffff;
    BOOST_CHECK_EQUAL(CalculateNextWorkRequired(&pindexLast, nLastRetargetTime, params), 0x1d00d86a);
}

/* Test the constraint on the upper bound for next work */
BOOST_AUTO_TEST_CASE(get_next_work_pow_limit)
{
    const Consensus::Params params = BitcoinRetargetParams();
    int64_t nLastRetargetTime = 1231006505; // Block #0
    CBlockIndex pindexLast;
    pindexLast.nHeight = 2015;
    pindexLast.nTime = 1233061996;  // Block #2015
    pindexLast.nBits = 0x1d00ffff;
    BOOST_CHECK_EQUAL(CalculateNextWorkRequired(&pindexLast, nLastRetargetTime, params), 0x1d00ffff);
}

/* Test the constraint on the lower bound for actual time taken */
BOOST_AUTO_TEST_CASE(get_next_work_lower_limit_actual)
{
    const Consensus::Params params = BitcoinRetargetParams();
    int64_t nLastRetargetTime = 1279008237; // Block #66528
    CBlockIndex pindexLast;
    pindexLast.nHeight = 68543;
    pindexLast.nTime = 1279297671;  // Block #68543
    pindexLast.nBits = 0x1c05a3f4;
    BOOST_CHECK_EQUAL(CalculateNextWorkRequired(&pindexLast, nLastRetargetTime, params), 0x1c0168fd);
}

/* Test the constraint on the upper bound for actual time taken */
BOOST_AUTO_TEST_CASE(get_next_work_upper_limit_actual)
{
    const Consensus::Params params = BitcoinRetargetParams();
    int64_t nLastRetargetTime = 1263163443; // NOTE: Not an actual block time
    CBlockIndex pindexLast;
    pindexLast.nHeight = 46367;
    pindexLast.nTime = 1269211443;  // Block #46367
    pindexLast.nBits = 0x1c387f6f;
    BOOST_CHECK_EQUAL(CalculateNextWorkRequired(&pindexLast, nLastRetargetTime, params), 0x1d00e1fd);
}

/* Build a chain where every block has the given nBits and the given solve time. */
static void BuildChain(std::vector<CBlockIndex>& blocks, uint32_t nBits, int64_t solvetime)
{
    for (size_t i = 0; i < blocks.size(); i++) {
        blocks[i].pprev = i ? &blocks[i - 1] : nullptr;
        blocks[i].nHeight = i;
        blocks[i].nTime = 1790294400 + i * solvetime;
        blocks[i].nBits = nBits;
        blocks[i].BuildSkip();
    }
}

/* Integer division and the compact encoding may only round the target down, by well under 0.1%. */
static void CheckTargetNear(uint32_t nBits, const arith_uint256& expected)
{
    arith_uint256 actual;
    actual.SetCompact(nBits);
    BOOST_CHECK(actual <= expected);
    BOOST_CHECK(actual >= expected - expected / 1000);
}

BOOST_AUTO_TEST_CASE(lwma_pow_limit_until_window_filled)
{
    const Consensus::Params& params = CreateChainParams(CBaseChainParams::MAIN)->GetConsensus();
    const unsigned int nPowLimit = UintToArith256(params.powLimit).GetCompact();
    std::vector<CBlockIndex> blocks(params.nLwmaAveragingWindow);
    BuildChain(blocks, 0x1c0ffff0, 1);
    CBlockHeader next;
    BOOST_CHECK_EQUAL(GetNextWorkRequired(&blocks.back(), &next, params), nPowLimit);
}

BOOST_AUTO_TEST_CASE(lwma_steady_hashrate_keeps_difficulty)
{
    const Consensus::Params& params = CreateChainParams(CBaseChainParams::MAIN)->GetConsensus();
    std::vector<CBlockIndex> blocks(params.nLwmaAveragingWindow + 50);
    BuildChain(blocks, 0x1c0ffff0, params.nPowTargetSpacing);
    CBlockHeader next;
    CheckTargetNear(GetNextWorkRequired(&blocks.back(), &next, params), arith_uint256().SetCompact(0x1c0ffff0));
}

BOOST_AUTO_TEST_CASE(lwma_fast_blocks_raise_difficulty)
{
    const Consensus::Params& params = CreateChainParams(CBaseChainParams::MAIN)->GetConsensus();
    std::vector<CBlockIndex> blocks(params.nLwmaAveragingWindow + 1);
    BuildChain(blocks, 0x1c0ffff0, params.nPowTargetSpacing / 2);
    CheckTargetNear(LwmaCalculateNextWorkRequired(&blocks.back(), params), arith_uint256().SetCompact(0x1c0ffff0) / 2);
}

BOOST_AUTO_TEST_CASE(lwma_slow_blocks_capped_at_six_times_spacing)
{
    const Consensus::Params& params = CreateChainParams(CBaseChainParams::MAIN)->GetConsensus();
    std::vector<CBlockIndex> blocks(params.nLwmaAveragingWindow + 1);
    BuildChain(blocks, 0x1c0ffff0, params.nPowTargetSpacing * 10);
    CheckTargetNear(LwmaCalculateNextWorkRequired(&blocks.back(), params), arith_uint256().SetCompact(0x1c0ffff0) * 6);
}

BOOST_AUTO_TEST_CASE(lwma_never_easier_than_pow_limit)
{
    const Consensus::Params& params = CreateChainParams(CBaseChainParams::MAIN)->GetConsensus();
    const unsigned int nPowLimit = UintToArith256(params.powLimit).GetCompact();
    std::vector<CBlockIndex> blocks(params.nLwmaAveragingWindow + 1);
    BuildChain(blocks, nPowLimit, params.nPowTargetSpacing * 10);
    BOOST_CHECK_EQUAL(LwmaCalculateNextWorkRequired(&blocks.back(), params), nPowLimit);
}

BOOST_AUTO_TEST_CASE(lwma_non_increasing_timestamps_count_as_one_second)
{
    const Consensus::Params& params = CreateChainParams(CBaseChainParams::MAIN)->GetConsensus();
    std::vector<CBlockIndex> blocks(params.nLwmaAveragingWindow + 1);
    BuildChain(blocks, 0x1c0ffff0, 0);
    // Every block claims the same time, so each counts as a 1 second solve time.
    CheckTargetNear(LwmaCalculateNextWorkRequired(&blocks.back(), params), arith_uint256().SetCompact(0x1c0ffff0) / params.nPowTargetSpacing);
}

/* Official yespower 1.0 test vectors (TESTS-OK of the yespower release), input bytes i*3. */
BOOST_AUTO_TEST_CASE(yespower_test_vectors)
{
    uint8_t src[80];
    for (size_t i = 0; i < sizeof(src); i++) src[i] = i * 3;

    const yespower_params_t no_pers = {YESPOWER_1_0, 2048, 8, nullptr, 0};
    yespower_binary_t hash;
    BOOST_CHECK_EQUAL(yespower_tls(src, sizeof(src), &no_pers, &hash), 0);
    BOOST_CHECK_EQUAL(HexStr(hash.uc, hash.uc + 32), "69e0e895b3df7aeeb837d71fe199e9d34f7ec46ecbca7a2c4308e51857ae9b46");

    const char* pers = "personality test";
    const yespower_params_t with_pers = {YESPOWER_1_0, 1024, 32, reinterpret_cast<const uint8_t*>(pers), strlen(pers)};
    BOOST_CHECK_EQUAL(yespower_tls(src, sizeof(src), &with_pers, &hash), 0);
    BOOST_CHECK_EQUAL(HexStr(hash.uc, hash.uc + 32), "1f0269acf565c49adc0ef9b8f26ab3808cdc38394a254fddeedcc3aacff6ad9d");
}

BOOST_AUTO_TEST_CASE(genesis_proof_of_work)
{
    for (const std::string& chain : {CBaseChainParams::MAIN, CBaseChainParams::TESTNET}) {
        const auto chainParams = CreateChainParams(chain);
        const Consensus::Params& params = chainParams->GetConsensus();
        CBlockHeader genesis = chainParams->GenesisBlock().GetBlockHeader();
        BOOST_CHECK(params.fPowYespower);
        BOOST_CHECK(GetBlockPoWHash(genesis, params) != genesis.GetHash());
        BOOST_CHECK(CheckBlockProofOfWork(genesis, params));
        // Any change to the header invalidates the work.
        genesis.nNonce++;
        BOOST_CHECK(!CheckBlockProofOfWork(genesis, params));
    }
    // Regtest keeps SHA256d so the functional tests can build blocks in Python.
    const auto regtest = CreateChainParams(CBaseChainParams::REGTEST);
    const CBlockHeader genesis = regtest->GenesisBlock().GetBlockHeader();
    BOOST_CHECK(!regtest->GetConsensus().fPowYespower);
    BOOST_CHECK(GetBlockPoWHash(genesis, regtest->GetConsensus()) == genesis.GetHash());
    BOOST_CHECK(CheckBlockProofOfWork(genesis, regtest->GetConsensus()));
}

BOOST_AUTO_TEST_CASE(GetBlockProofEquivalentTime_test)
{
    const auto chainParams = CreateChainParams(CBaseChainParams::MAIN);
    std::vector<CBlockIndex> blocks(10000);
    for (int i = 0; i < 10000; i++) {
        blocks[i].pprev = i ? &blocks[i - 1] : nullptr;
        blocks[i].nHeight = i;
        blocks[i].nTime = 1269211443 + i * chainParams->GetConsensus().nPowTargetSpacing;
        blocks[i].nBits = 0x207fffff; /* target 0x7fffff000... */
        blocks[i].nChainWork = i ? blocks[i - 1].nChainWork + GetBlockProof(blocks[i - 1]) : arith_uint256(0);
    }

    for (int j = 0; j < 1000; j++) {
        CBlockIndex *p1 = &blocks[InsecureRandRange(10000)];
        CBlockIndex *p2 = &blocks[InsecureRandRange(10000)];
        CBlockIndex *p3 = &blocks[InsecureRandRange(10000)];

        int64_t tdiff = GetBlockProofEquivalentTime(*p1, *p2, *p3, chainParams->GetConsensus());
        BOOST_CHECK_EQUAL(tdiff, p1->GetBlockTime() - p2->GetBlockTime());
    }
}

BOOST_AUTO_TEST_SUITE_END()
