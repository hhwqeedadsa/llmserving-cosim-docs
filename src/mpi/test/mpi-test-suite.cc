/*
 * Copyright (c) 2018 Lawrence Livermore National Laboratory
 *
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * Author: Peter D. Barnes, Jr. <pdbarnes@llnl.gov>
 */

#include "ns3/example-as-test.h"

#include <cstdlib>
#include <fstream>
#include <sstream>

using namespace ns3;

/**
 * @ingroup mpi-tests
 *
 * This version of ns3::ExampleTestCase is specialized for MPI
 * by accepting the number of ranks as a parameter,
 * then building a `--command-template` string which
 * invokes `mpiexec` correctly to execute MPI examples.
 */
class MpiTestCase : public ExampleAsTestCase
{
  public:
    /**
     * @copydoc ns3::ExampleAsTestCase::ExampleAsTestCase
     *
     * @param [in] ranks The number of ranks to use
     */
    MpiTestCase(const std::string name,
                const std::string program,
                const std::string dataDir,
                const int ranks,
                const std::string args = "",
                const bool shouldNotErr = true);

    /** Destructor */
    ~MpiTestCase() override
    {
    }

    /**
     * Produce the `--command-template` argument which will invoke
     * `mpiexec` with the requested number of ranks.
     *
     * @returns The `--command-template` string.
     */
    std::string GetCommandTemplate() const override;

    /**
     * Sort the output from parallel execution.
     * stdout from multiple ranks is not ordered.
     *
     * @returns Sort command
     */
    std::string GetPostProcessingCommand() const override;

  private:
    /** The number of ranks. */
    int m_ranks;
};

MpiTestCase::MpiTestCase(const std::string name,
                         const std::string program,
                         const std::string dataDir,
                         const int ranks,
                         const std::string args /* = "" */,
                         const bool shouldNotErr /* = true */)
    : ExampleAsTestCase(name, program, dataDir, args, shouldNotErr),
      m_ranks(ranks)
{
}

std::string
MpiTestCase::GetCommandTemplate() const
{
    std::stringstream ss;
    ss << "mpiexec -n " << m_ranks << " %s --test " << m_args;
    return ss.str();
}

std::string
MpiTestCase::GetPostProcessingCommand() const
{
    std::string command("| grep TEST | sort ");
    return command;
}

/**
 * @ingroup mpi-tests
 * MPI specialization of ns3::ExampleTestSuite.
 */
class MpiTestSuite : public TestSuite
{
  public:
    /**
     * @copydoc MpiTestCase::MpiTestCase
     *
     * @param [in] duration Amount of time this test takes to execute
     *             (defaults to QUICK).
     */
    MpiTestSuite(const std::string name,
                 const std::string program,
                 const std::string dataDir,
                 const int ranks,
                 const std::string args = "",
                 const Duration duration = Duration::QUICK,
                 const bool shouldNotErr = true)
        : TestSuite(name, Type::EXAMPLE)
    {
        AddTestCase(new MpiTestCase(name, program, dataDir, ranks, args, shouldNotErr), duration);
    }

}; // class MpiTestSuite

class MpiRemoteTpRegressionDeprecatedInterceptorFailsFastTestCase : public TestCase
{
  public:
    MpiRemoteTpRegressionDeprecatedInterceptorFailsFastTestCase()
        : TestCase("mpi-example-ub-mtp-remote-tp-regression-deprecated-interceptor-fails-fast-np2")
    {
    }

    void DoRun() override
    {
        SetDataDir(NS_TEST_SOURCEDIR);
        const std::string testFile = CreateTempDirFilename(GetName() + ".log");
        const std::string command =
            "python3 ./ns3 run src/unified-bus/examples/ub-mtp-remote-tp-regression --no-build "
            "--command-template=\"mpiexec -n 2 %s --test --mode=interceptor\" > " +
            testFile + " 2>&1";

        const int status = std::system(command.c_str());

        std::ifstream input(testFile);
        std::stringstream buffer;
        buffer << input.rdbuf();
        const std::string output = buffer.str();

        NS_TEST_ASSERT_MSG_NE(status,
                              0,
                              "interceptor-removed regression should fail fast when deprecated mode is requested");
        NS_TEST_ASSERT_MSG_NE(output.find("TEST ERROR interceptor mode has been removed; use tp"),
                              std::string::npos,
                              "deprecated mode should emit the expected error message");
    }
};

class MpiRemoteTpRegressionDeprecatedInterceptorFailsFastTestSuite : public TestSuite
{
  public:
    MpiRemoteTpRegressionDeprecatedInterceptorFailsFastTestSuite()
        : TestSuite("mpi-example-ub-mtp-remote-tp-regression-deprecated-interceptor-fails-fast-np2",
                    Type::SYSTEM)
    {
        AddTestCase(new MpiRemoteTpRegressionDeprecatedInterceptorFailsFastTestCase(),
                    TestCase::Duration::QUICK);
    }
};

class MpiUbQuickExampleRunTestCase : public TestCase
{
  public:
    MpiUbQuickExampleRunTestCase(const std::string& name, const std::string& args)
        : TestCase(name),
          m_args(args)
    {
    }

    void DoRun() override
    {
        SetDataDir(NS_TEST_SOURCEDIR);
        const std::string testFile = CreateTempDirFilename(GetName() + ".log");
        const std::string command =
            "python3 ./ns3 run src/unified-bus/examples/ub-quick-example --no-build "
            "--command-template=\"mpiexec -n 2 %s --test " +
            m_args + "\" > " + testFile + " 2>&1";

        const int status = std::system(command.c_str());

        std::ifstream input(testFile);
        std::stringstream buffer;
        buffer << input.rdbuf();
        const std::string output = buffer.str();

        NS_TEST_ASSERT_MSG_EQ(status,
                              0,
                              "MPI quick-example case should run successfully");
        NS_TEST_ASSERT_MSG_NE(output.find("TEST : 00000 : PASSED"),
                              std::string::npos,
                              "MPI quick-example case should report PASSED");
    }

  private:
    std::string m_args;
};

class MpiUbQuickExampleRunTestSuite : public TestSuite
{
  public:
    MpiUbQuickExampleRunTestSuite(const std::string& name, const std::string& args)
        : TestSuite(name, Type::SYSTEM)
    {
        AddTestCase(new MpiUbQuickExampleRunTestCase(name, args), TestCase::Duration::QUICK);
    }
};

/* Tests using SimpleDistributedSimulatorImpl */
static MpiTestSuite g_mpiNms2("mpi-example-nms-2", "nms-p2p-nix-distributed", NS_TEST_SOURCEDIR, 2);
static MpiTestSuite g_mpiComm2("mpi-example-comm-2",
                               "simple-distributed-mpi-comm",
                               NS_TEST_SOURCEDIR,
                               2);
static MpiTestSuite g_mpiComm2comm("mpi-example-comm-2-init",
                                   "simple-distributed-mpi-comm",
                                   NS_TEST_SOURCEDIR,
                                   2,
                                   "--init");
static MpiTestSuite g_mpiComm3comm("mpi-example-comm-3-init",
                                   "simple-distributed-mpi-comm",
                                   NS_TEST_SOURCEDIR,
                                   3,
                                   "--init");
static MpiTestSuite g_mpiEmpty2("mpi-example-empty-2",
                                "simple-distributed-empty-node",
                                NS_TEST_SOURCEDIR,
                                2);
static MpiTestSuite g_mpiEmpty3("mpi-example-empty-3",
                                "simple-distributed-empty-node",
                                NS_TEST_SOURCEDIR,
                                3);
static MpiTestSuite g_mpiSimple2("mpi-example-simple-2",
                                 "simple-distributed",
                                 NS_TEST_SOURCEDIR,
                                 2);
static MpiTestSuite g_mpiThird2("mpi-example-third-2", "third-distributed", NS_TEST_SOURCEDIR, 2);
static MpiUbQuickExampleRunTestSuite g_mpiUbConfigSmoke2(
    "mpi-example-ub-quick-example-run-mpi-minimal-2",
    "--case-path=scratch/ub-mpi-minimal --stop-ms=50");

#ifdef NS3_MTP
static MpiUbQuickExampleRunTestSuite g_mpiUbConfigHybridSmoke2(
    "mpi-example-ub-quick-example-run-hybrid-minimal-2",
    "--case-path=scratch/ub-mpi-minimal --mtp-threads=2 --stop-ms=50");
static MpiUbQuickExampleRunTestSuite g_mpiUbConfigHybridLdst2(
    "mpi-example-ub-quick-example-run-hybrid-ldst-2",
    "--case-path=scratch/ub-mpi-minimal --mtp-threads=2 --stop-ms=50");
static MpiUbQuickExampleRunTestSuite g_mpiUbConfigHybridMultiRemote2(
    "mpi-example-ub-quick-example-run-hybrid-multi-remote-2",
    "--case-path=scratch/ub-mpi-minimal --mtp-threads=2 --stop-ms=50");
static MpiTestSuite g_mpiUbRemoteTpRegressionNp2(
    "mpi-example-ub-mtp-remote-tp-regression-np2",
    "src/unified-bus/examples/ub-mtp-remote-tp-regression",
    NS_TEST_SOURCEDIR,
    2);
static MpiRemoteTpRegressionDeprecatedInterceptorFailsFastTestSuite
    g_mpiUbRemoteTpRegressionDeprecatedInterceptorFailsFast;
#endif

/* Tests using NullMessageSimulatorImpl */
static MpiTestSuite g_mpiSimple2NullMsg("mpi-example-simple-2-nullmsg",
                                        "simple-distributed",
                                        NS_TEST_SOURCEDIR,
                                        2,
                                        "--nullmsg");
static MpiTestSuite g_mpiEmpty2NullMsg("mpi-example-empty-2-nullmsg",
                                       "simple-distributed-empty-node",
                                       NS_TEST_SOURCEDIR,
                                       2,
                                       "-nullmsg");
static MpiTestSuite g_mpiEmpty3NullMsg("mpi-example-empty-3-nullmsg",
                                       "simple-distributed-empty-node",
                                       NS_TEST_SOURCEDIR,
                                       3,
                                       "-nullmsg");
