#pragma once

#include <limits>

namespace export_dimensions
{

inline bool roundUpToMultiple( int value, int multiple, int *rounded )
{
    if( rounded == nullptr || value <= 0 || multiple <= 0 ) return false;

    const int remainder = value % multiple;
    if( remainder == 0 )
    {
        *rounded = value;
        return true;
    }

    const int increment = multiple - remainder;
    if( value > std::numeric_limits<int>::max() - increment ) return false;

    *rounded = value + increment;
    return true;
}

} // namespace export_dimensions
