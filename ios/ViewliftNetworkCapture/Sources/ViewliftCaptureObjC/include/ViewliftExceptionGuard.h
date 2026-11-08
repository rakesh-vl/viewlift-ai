#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

/// Runs `block` and returns the Objective-C exception it raised, or nil when it finished normally.
FOUNDATION_EXPORT NSException *_Nullable VLCCatchException(NS_NOESCAPE void (^block)(void));

NS_ASSUME_NONNULL_END
