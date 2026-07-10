/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

#include "ns3/test.h"

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <utility>

using namespace ns3;

namespace
{

std::pair<int, std::string>
RunMtpExample(const std::string& testFile,
              const std::string& program,
              const std::string& arguments = "")
{
    const std::filesystem::path repoRoot = PROJECT_SOURCE_PATH;
    const std::string pythonCommand =
        std::system("command -v python3.12 >/dev/null 2>&1") == 0 ? "python3.12" : "python3";

    std::string command = "cd \"" + repoRoot.string() + "\" && " + pythonCommand +
                          " ./ns3 run " + program +
                          " --no-build --command-template=\"%s --test";
    if (!arguments.empty())
    {
        command += " " + arguments;
    }
    command += "\" > \"" + testFile + "\" 2>&1";

    const int status = std::system(command.c_str());
    std::ifstream input(testFile);
    std::stringstream buffer;
    buffer << input.rdbuf();
    return {status, buffer.str()};
}

class MtpExpectedFailureSystemTestCase : public TestCase
{
  public:
    MtpExpectedFailureSystemTestCase(const std::string& name,
                                     const std::string& program,
                                     const std::string& expectedDiagnostic)
        : TestCase(name),
          m_program(program),
          m_expectedDiagnostic(expectedDiagnostic)
    {
    }

    void DoRun() override
    {
        const std::string testFile = CreateTempDirFilename(GetName() + ".log");
        const auto [status, output] = RunMtpExample(testFile, m_program);

        NS_TEST_ASSERT_MSG_NE(status, 0, "invalid MTP runtime input must fail fast");
        NS_TEST_ASSERT_MSG_NE(output.find(m_expectedDiagnostic),
                              std::string::npos,
                              "failure must report the violated MTP invariant: " << output);
    }

  private:
    std::string m_program;
    std::string m_expectedDiagnostic;
};

class MtpManualPartitionValidSystemTestCase : public TestCase
{
  public:
    MtpManualPartitionValidSystemTestCase()
        : TestCase("MTP - manual partition schedules a node on its worker LP")
    {
    }

    void DoRun() override
    {
        const std::string testFile = CreateTempDirFilename(GetName() + ".log");
        const auto [status, output] =
            RunMtpExample(testFile,
                          "src/mtp/examples/mtp-manual-partition-validation",
                          "--valid");

        NS_TEST_ASSERT_MSG_EQ(status, 0, "valid manual MTP partition must run: " << output);
        NS_TEST_ASSERT_MSG_NE(output.find("lp=1"),
                              std::string::npos,
                              "node event must execute on worker LP 1: " << output);
    }
};

class MtpRemoteEventOrderingSystemTestCase : public TestCase
{
  public:
    MtpRemoteEventOrderingSystemTestCase()
        : TestCase("MTP - same-time remote events preserve sender order")
    {
    }

    void DoRun() override
    {
        const std::string testFile = CreateTempDirFilename(GetName() + ".log");
        const auto [status, output] =
            RunMtpExample(testFile, "src/mtp/examples/mtp-remote-event-ordering");

        NS_TEST_ASSERT_MSG_EQ(status, 0, "same-time remote events must run: " << output);
        NS_TEST_ASSERT_MSG_NE(output.find("order=12 wrong-lp=0"),
                              std::string::npos,
                              "remote events must preserve sender order on LP 2: " << output);
    }
};

class MtpManualPartitionValidationSystemTestSuite : public TestSuite
{
  public:
    MtpManualPartitionValidationSystemTestSuite()
        : TestSuite("mtp-manual-partition-validation", Type::SYSTEM)
    {
        AddTestCase(new MtpManualPartitionValidSystemTestCase(), TestCase::Duration::QUICK);
        AddTestCase(
            new MtpExpectedFailureSystemTestCase("MTP - manual partition rejects an unmapped node",
                                                 "src/mtp/examples/mtp-manual-partition-validation",
                                                 "MTP local LP mapping is missing for node"),
            TestCase::Duration::QUICK);
    }
};

MtpManualPartitionValidationSystemTestSuite g_manualPartitionValidation;

class MtpRemoteEventOrderingSystemTestSuite : public TestSuite
{
  public:
    MtpRemoteEventOrderingSystemTestSuite()
        : TestSuite("mtp-remote-event-ordering", Type::SYSTEM)
    {
        AddTestCase(new MtpRemoteEventOrderingSystemTestCase(), TestCase::Duration::QUICK);
    }
};

MtpRemoteEventOrderingSystemTestSuite g_remoteEventOrdering;
} // namespace
