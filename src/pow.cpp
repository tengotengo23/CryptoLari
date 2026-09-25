// Copyright (c) 2009-2010 Satoshi Nakamoto
// Copyright (c) 2009-2016 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#include <pow.h>

#include <arith_uint256.h>
#include <chain.h>
#include <crypto/yespower/yespower.h>
#include <primitives/block.h>
#include <streams.h>
#include <uint256.h>
#include <version.h>

#include <algorithm>
#include <cstdlib>
#include <cstring>

unsigned int GetNextWorkRequired(const CBlockIndex* pindexLast, const CBlockHeader *pblock, const Consensus::Params& params)
{
    assert(pindexLast != nullptr);
    if (params.nLwmaAveragingWindow > 0)
        return LwmaCalculateNextWorkRequired(pindexLast, params);

    unsigned int nProofOfWorkLimit = UintToArith256(params.powLimit).GetCompact();

    // Only change once per difficulty adjustment interval
    if ((pindexLast->nHeight+1) % params.DifficultyAdjustmentInterval() != 0)
    {
        if (params.fPowAllowMinDifficultyBlocks)
        {
            // Special difficulty rule for testnet:
            // If the new block's timestamp is more than 2* 10 minutes
            // then allow mining of a min-difficulty block.
            if (pblock->GetBlockTime() > pindexLast->GetBlockTime() + params.nPowTargetSpacing*2)
                return nProofOfWorkLimit;
            else
            {
                // Return the last non-special-min-difficulty-rules-block
                const CBlockIndex* pindex = pindexLast;
                while (pindex->pprev && pindex->nHeight % params.DifficultyAdjustmentInterval() != 0 && pindex->nBits == nProofOfWorkLimit)
                    pindex = pindex->pprev;
                return pindex->nBits;
            }
        }
        return pindexLast->nBits;
    }

    // Go back by what we want to be 14 days worth of blocks
    int nHeightFirst = pindexLast->nHeight - (params.DifficultyAdjustmentInterval()-1);
    assert(nHeightFirst >= 0);
    const CBlockIndex* pindexFirst = pindexLast->GetAncestor(nHeightFirst);
    assert(pindexFirst);

    return CalculateNextWorkRequired(pindexLast, pindexFirst->GetBlockTime(), params);
}

unsigned int CalculateNextWorkRequired(const CBlockIndex* pindexLast, int64_t nFirstBlockTime, const Consensus::Params& params)
{
    if (params.fPowNoRetargeting)
        return pindexLast->nBits;

    // Limit adjustment step
    int64_t nActualTimespan = pindexLast->GetBlockTime() - nFirstBlockTime;
    if (nActualTimespan < params.nPowTargetTimespan/4)
        nActualTimespan = params.nPowTargetTimespan/4;
    if (nActualTimespan > params.nPowTargetTimespan*4)
        nActualTimespan = params.nPowTargetTimespan*4;

    // Retarget
    const arith_uint256 bnPowLimit = UintToArith256(params.powLimit);
    arith_uint256 bnNew;
    bnNew.SetCompact(pindexLast->nBits);
    bnNew *= nActualTimespan;
    bnNew /= params.nPowTargetTimespan;

    if (bnNew > bnPowLimit)
        bnNew = bnPowLimit;

    return bnNew.GetCompact();
}

/**
 * LWMA-1 difficulty algorithm by zawy12 (https://github.com/zawy12/difficulty-algorithms/issues/3).
 *
 * Retargets every block from the last N solve times, giving linearly more
 * weight to recent blocks. A small chain needs this: with Bitcoin's 2016-block
 * retarget, a large miner who joins and then leaves can stall the chain for weeks.
 */
unsigned int LwmaCalculateNextWorkRequired(const CBlockIndex* pindexLast, const Consensus::Params& params)
{
    const int64_t T = params.nPowTargetSpacing;
    const int64_t N = params.nLwmaAveragingWindow;
    // Normalizes the weighted solve time sum so that on-target blocks keep the target unchanged.
    const int64_t k = N * (N + 1) * T / 2;
    const int64_t height = pindexLast->nHeight;
    const arith_uint256 powLimit = UintToArith256(params.powLimit);

    // Not enough history yet: the first N blocks are mined at minimum difficulty.
    if (height < N)
        return powLimit.GetCompact();

    arith_uint256 avgTarget;
    int64_t sumWeightedSolvetimes = 0;
    int64_t previousTimestamp = pindexLast->GetAncestor(height - N)->GetBlockTime();

    for (int64_t i = height - N + 1, weight = 1; i <= height; i++, weight++) {
        const CBlockIndex* block = pindexLast->GetAncestor(i);

        // Treat out-of-order timestamps as a 1 second solve time so solve times are never
        // negative, and cap long ones at 6*T to avoid oscillation after a stall.
        const int64_t thisTimestamp = std::max(block->GetBlockTime(), previousTimestamp + 1);
        const int64_t solvetime = std::min(6 * T, thisTimestamp - previousTimestamp);
        previousTimestamp = thisTimestamp;

        sumWeightedSolvetimes += solvetime * weight;

        arith_uint256 target;
        target.SetCompact(block->nBits);
        // Dividing before summing keeps the multiplication below from overflowing.
        avgTarget += target / arith_uint256(N) / arith_uint256(k);
    }

    arith_uint256 nextTarget = avgTarget * arith_uint256(sumWeightedSolvetimes);
    if (nextTarget > powLimit)
        nextTarget = powLimit;

    return nextTarget.GetCompact();
}

bool CheckProofOfWork(uint256 hash, unsigned int nBits, const Consensus::Params& params)
{
    bool fNegative;
    bool fOverflow;
    arith_uint256 bnTarget;

    bnTarget.SetCompact(nBits, &fNegative, &fOverflow);

    // Check range
    if (fNegative || bnTarget == 0 || fOverflow || bnTarget > UintToArith256(params.powLimit))
        return false;

    // Check proof of work matches claimed amount
    if (UintToArith256(hash) > bnTarget)
        return false;

    return true;
}

uint256 GetBlockPoWHash(const CBlockHeader& block, const Consensus::Params& params)
{
    if (!params.fPowYespower)
        return block.GetHash();

    // yespower 1.0 with 2 MiB of memory (N = 2048, r = 8): about 1 ms per hash
    // on a desktop core, and designed so that GPUs and ASICs gain little over
    // CPUs. The personalization string keeps hashrate that is set up for other
    // yespower coins from mining CryptoLari as-is.
    static const yespower_params_t yespower_params = {
        YESPOWER_1_0, 2048, 8, reinterpret_cast<const uint8_t*>("CryptoLari"), 10};

    std::vector<unsigned char> header;
    CVectorWriter(SER_NETWORK, PROTOCOL_VERSION, header, 0, block);

    yespower_binary_t hash;
    if (yespower_tls(header.data(), header.size(), &yespower_params, &hash) != 0) {
        // This only fails when 2 MiB cannot be allocated. Carrying on could get a
        // valid block marked invalid and fork this node off the network.
        std::abort();
    }

    uint256 result;
    static_assert(sizeof(hash.uc) == 32, "yespower hash must fill a uint256");
    memcpy(result.begin(), hash.uc, sizeof(hash.uc));
    return result;
}

bool CheckBlockProofOfWork(const CBlockHeader& block, const Consensus::Params& params)
{
    return CheckProofOfWork(GetBlockPoWHash(block, params), block.nBits, params);
}
