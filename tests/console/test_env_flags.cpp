#include "../common/minitest.h"
#include "../../src/batch/EnvFlags.h"

TEST(EnvFlags, EmptyIsFalse)
{
    ASSERT_FALSE(mlvappEnvFlagEnabled(QByteArrayLiteral("")));
}

TEST(EnvFlags, ZeroIsFalse)
{
    ASSERT_FALSE(mlvappEnvFlagEnabled(QByteArrayLiteral("0")));
}

TEST(EnvFlags, FalseIsFalse)
{
    ASSERT_FALSE(mlvappEnvFlagEnabled(QByteArrayLiteral("false")));
}

TEST(EnvFlags, OneIsTrue)
{
    ASSERT_TRUE(mlvappEnvFlagEnabled(QByteArrayLiteral("1")));
}

TEST(EnvFlags, UppercaseTrueIsTrue)
{
    ASSERT_TRUE(mlvappEnvFlagEnabled(QByteArrayLiteral("TRUE")));
}

TEST(EnvFlags, OnIsTrue)
{
    ASSERT_TRUE(mlvappEnvFlagEnabled(QByteArrayLiteral("on")));
}

TEST(EnvFlags, WhitespaceIsTrimmed)
{
    ASSERT_TRUE(mlvappEnvFlagEnabled(QByteArrayLiteral(" 1 ")));
}

TEST(EnvFlags, TwoIsFalse)
{
    ASSERT_FALSE(mlvappEnvFlagEnabled(QByteArrayLiteral("2")));
}

TEST(EnvFlags, YIsFalse)
{
    ASSERT_FALSE(mlvappEnvFlagEnabled(QByteArrayLiteral("y")));
}

TEST(EnvFlags, NoIsFalse)
{
    ASSERT_FALSE(mlvappEnvFlagEnabled(QByteArrayLiteral("no")));
}

TEST(EnvFlags, NullIsFalse)
{
    ASSERT_FALSE(mlvappEnvFlagEnabled(QByteArray()));
}

TEST(EnvFlags, UnrecognizedWordIsFalse)
{
    ASSERT_FALSE(mlvappEnvFlagEnabled(QByteArrayLiteral("enable")));
}
