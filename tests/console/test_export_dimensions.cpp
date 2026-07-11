#include "../common/minitest.h"
#include "../../platform/qt/ExportDimensions.h"

#include <limits>

TEST( ExportDimensions, ExactMultiplesRemainUnchanged )
{
    int rounded = 0;
    ASSERT_TRUE( export_dimensions::roundUpToMultiple( 16, 16, &rounded ) );
    ASSERT_EQ( 16, rounded );
    ASSERT_TRUE( export_dimensions::roundUpToMultiple( 1920, 16, &rounded ) );
    ASSERT_EQ( 1920, rounded );
}

TEST( ExportDimensions, EveryRemainderRoundsToTheNextMultipleOf16 )
{
    for( int remainder = 1; remainder < 16; ++remainder )
    {
        int rounded = 0;
        ASSERT_TRUE( export_dimensions::roundUpToMultiple( 1920 + remainder, 16, &rounded ) );
        ASSERT_EQ( 1936, rounded );
    }
}

TEST( ExportDimensions, RepresentativeStretchedWidthsRoundCorrectly )
{
    const int inputs[] = { 1921, 1922, 1928, 1935 };
    for( int value : inputs )
    {
        int rounded = 0;
        ASSERT_TRUE( export_dimensions::roundUpToMultiple( value, 16, &rounded ) );
        ASSERT_EQ( 1936, rounded );
    }
}

TEST( ExportDimensions, InvalidAndOverflowingInputsFailWithoutChangingOutput )
{
    int rounded = 77;
    ASSERT_FALSE( export_dimensions::roundUpToMultiple( 0, 16, &rounded ) );
    ASSERT_FALSE( export_dimensions::roundUpToMultiple( -1, 16, &rounded ) );
    ASSERT_FALSE( export_dimensions::roundUpToMultiple( 16, 0, &rounded ) );
    ASSERT_FALSE( export_dimensions::roundUpToMultiple( 16, -1, &rounded ) );
    ASSERT_FALSE( export_dimensions::roundUpToMultiple( 16, 16, nullptr ) );
    ASSERT_FALSE( export_dimensions::roundUpToMultiple( std::numeric_limits<int>::max(), 16, &rounded ) );
    ASSERT_EQ( 77, rounded );
}
