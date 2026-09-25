// Copyright (c) 2014-2016 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#include <chainparams.h>
#include <validation.h>
#include <net.h>

#include <test/test_bitcoin.h>

#include <boost/signals2/signal.hpp>
#include <boost/test/unit_test.hpp>

BOOST_FIXTURE_TEST_SUITE(main_tests, TestingSetup)

static void TestBlockSubsidyHalvings(const Consensus::Params& consensusParams)
{
    int maxHalvings = 64;
    CAmount nInitialSubsidy = consensusParams.nInitialSubsidy;

    CAmount nPreviousSubsidy = nInitialSubsidy * 2; // for height == 0
    BOOST_CHECK_EQUAL(nPreviousSubsidy, nInitialSubsidy * 2);
    for (int nHalvings = 0; nHalvings < maxHalvings; nHalvings++) {
        int nHeight = nHalvings * consensusParams.nSubsidyHalvingInterval;
        CAmount nSubsidy = GetBlockSubsidy(nHeight, consensusParams);
        BOOST_CHECK(nSubsidy <= nInitialSubsidy);
        BOOST_CHECK_EQUAL(nSubsidy, nPreviousSubsidy / 2);
        nPreviousSubsidy = nSubsidy;
    }
    BOOST_CHECK_EQUAL(GetBlockSubsidy(maxHalvings * consensusParams.nSubsidyHalvingInterval, consensusParams), 0);
}

static void TestBlockSubsidyHalvings(int nSubsidyHalvingInterval)
{
    Consensus::Params consensusParams;
    consensusParams.nSubsidyHalvingInterval = nSubsidyHalvingInterval;
    consensusParams.nInitialSubsidy = 50 * COIN;
    TestBlockSubsidyHalvings(consensusParams);
}

BOOST_AUTO_TEST_CASE(block_subsidy_test)
{
    const auto chainParams = CreateChainParams(CBaseChainParams::MAIN);
    TestBlockSubsidyHalvings(chainParams->GetConsensus()); // As in main
    TestBlockSubsidyHalvings(150); // As in regtest
    TestBlockSubsidyHalvings(1000); // Just another interval
}

BOOST_AUTO_TEST_CASE(subsidy_limit_test)
{
    const auto chainParams = CreateChainParams(CBaseChainParams::MAIN);
    CAmount nSum = 0;
    for (int nHeight = 0; nHeight < 28000000; nHeight += 1000) {
        CAmount nSubsidy = GetBlockSubsidy(nHeight, chainParams->GetConsensus());
        BOOST_CHECK(nSubsidy <= 100 * COIN);
        nSum += nSubsidy * 1000;
        BOOST_CHECK(MoneyRange(nSum));
    }
    BOOST_CHECK_EQUAL(nSum, 8399999995380000ULL);
}

BOOST_AUTO_TEST_CASE(dev_fee_test)
{
    const auto chainParams = CreateChainParams(CBaseChainParams::MAIN);
    const Consensus::Params& params = chainParams->GetConsensus();

    // 5% of the subsidy, following the halvings, starting at block 1
    BOOST_CHECK_EQUAL(params.nDevFeePercent, 5);
    BOOST_CHECK_EQUAL(GetDevFee(0, params), 0);
    BOOST_CHECK_EQUAL(GetDevFee(1, params), 5 * COIN);
    BOOST_CHECK_EQUAL(GetDevFee(params.nSubsidyHalvingInterval - 1, params), 5 * COIN);
    BOOST_CHECK_EQUAL(GetDevFee(params.nSubsidyHalvingInterval, params), 5 * COIN / 2);
    BOOST_CHECK_EQUAL(GetDevFee(64 * params.nSubsidyHalvingInterval, params), 0);

    const CScript devScript = GetDevFeeScript(params);
    const CScript otherScript = CScript() << OP_TRUE;

    CMutableTransaction coinbase;
    coinbase.vin.resize(1);
    coinbase.vin[0].prevout.SetNull();
    coinbase.vout.resize(2);
    coinbase.vout[0].scriptPubKey = otherScript;
    coinbase.vout[0].nValue = 95 * COIN;
    coinbase.vout[1].scriptPubKey = devScript;
    coinbase.vout[1].nValue = 5 * COIN;
    BOOST_CHECK(CheckDevFee(coinbase, 1, params));

    // Paying less than the dev fee is invalid
    coinbase.vout[1].nValue = 5 * COIN - 1;
    BOOST_CHECK(!CheckDevFee(coinbase, 1, params));

    // Several outputs to the dev script are summed up
    coinbase.vout.push_back(CTxOut(1, devScript));
    BOOST_CHECK(CheckDevFee(coinbase, 1, params));

    // Not paying the dev fee at all is invalid
    coinbase.vout.resize(1);
    coinbase.vout[0].nValue = 100 * COIN;
    BOOST_CHECK(!CheckDevFee(coinbase, 1, params));

    // ...except when no dev fee is due (the genesis block, or regtest by default)
    BOOST_CHECK(CheckDevFee(coinbase, 0, params));
    const auto regtestParams = CreateChainParams(CBaseChainParams::REGTEST);
    BOOST_CHECK_EQUAL(GetDevFee(1, regtestParams->GetConsensus()), 0);
    BOOST_CHECK(CheckDevFee(coinbase, 1, regtestParams->GetConsensus()));
}

bool ReturnFalse() { return false; }
bool ReturnTrue() { return true; }

BOOST_AUTO_TEST_CASE(test_combiner_all)
{
    boost::signals2::signal<bool (), CombinerAll> Test;
    BOOST_CHECK(Test());
    Test.connect(&ReturnFalse);
    BOOST_CHECK(!Test());
    Test.connect(&ReturnTrue);
    BOOST_CHECK(!Test());
    Test.disconnect(&ReturnFalse);
    BOOST_CHECK(Test());
    Test.disconnect(&ReturnTrue);
    BOOST_CHECK(Test());
}
BOOST_AUTO_TEST_SUITE_END()
