#ifndef DUALISOPATTERNMAPPING_H
#define DUALISOPATTERNMAPPING_H

inline int dualIsoUiPatternIndexFromCorePattern( int pattern )
{
    const int corePattern = pattern < 0 ? -pattern : pattern;
    if( corePattern >= 1 && corePattern <= 4 )
        return corePattern - 1;
    return 0;
}

inline int dualIsoCorePatternFromUiIndex( int index )
{
    if( index < 0 || index > 3 )
        return 0;
    return index + 1;
}

#endif
