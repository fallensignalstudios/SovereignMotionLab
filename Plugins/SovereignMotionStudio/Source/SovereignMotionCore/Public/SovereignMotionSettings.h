#pragma once

#include "CoreMinimal.h"
#include "Engine/DeveloperSettings.h"
#include "SovereignMotionSettings.generated.h"

/** Project Settings > Plugins > Sovereign Motion Studio. Stored per user, since the sidecar runs locally. */
UCLASS(config = EditorPerProjectUserSettings, meta = (DisplayName = "Sovereign Motion Studio"))
class SOVEREIGNMOTIONCORE_API USovereignMotionSettings : public UDeveloperSettings
{
	GENERATED_BODY()

public:
	/** Where `python -m sovereign_motion serve` is listening. */
	UPROPERTY(config, EditAnywhere, Category = "Sidecar")
	FString SidecarUrl = TEXT("http://127.0.0.1:8765");

	/** Seconds to wait for a generation before giving up. */
	UPROPERTY(config, EditAnywhere, Category = "Sidecar", meta = (ClampMin = "1"))
	float RequestTimeoutSeconds = 120.f;

	virtual FName GetCategoryName() const override { return TEXT("Plugins"); }
};
