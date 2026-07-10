#ifndef DUALISOPATTERNMAPPING_H
#define DUALISOPATTERNMAPPING_H

inline int dualIsoUiPatternIndexFromCorePattern( int pattern )
{
    const int corePattern = pattern < 0 ? -pattern : pattern;
    if( corePattern >= 1 && corePattern <= 4 )
        return corePattern;
    if( corePattern == 5 )
        return 5;
    return 0;
}

inline int dualIsoCorePatternFromUiIndex( int index )
{
    if( index >= 1 && index <= 4 )
        return index;
    if( index == 5 )
        return 5;
    return 0;
}

#endif
