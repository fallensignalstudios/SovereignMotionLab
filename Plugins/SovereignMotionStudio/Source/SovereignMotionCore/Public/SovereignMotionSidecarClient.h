#pragma once

#include "CoreMinimal.h"
#include "SovereignMotionTypes.h"

/**
 * Talks to the local Python sidecar over HTTP. Callbacks run on the game thread.
 * This is the only class that knows the model runs out of process; swapping to
 * in-engine inference later (NNE) means providing another implementation of these calls.
 */
class SOVEREIGNMOTIONCORE_API FSovereignMotionSidecarClient
{
public:
	using FHealthCallback = TFunction<void(bool bOk, const FString& Message)>;
	using FGenerateCallback = TFunction<void(bool bOk, const FSovereignMotionResult& Result, const FString& RawJson, const FString& Error)>;

	/** Uses the URL and timeout from USovereignMotionSettings. */
	FSovereignMotionSidecarClient();
	FSovereignMotionSidecarClient(const FString& InBaseUrl, float InTimeoutSeconds);

	/** The exact JSON Generate() sends, including the target skeleton's reference pose if one is set. */
	static FString BuildRequestJson(const FSovereignMotionRequest& Request);

	void CheckHealth(FHealthCallback OnComplete) const;
	void Generate(const FSovereignMotionRequest& Request, FGenerateCallback OnComplete) const;

private:
	FString BaseUrl;
	float TimeoutSeconds;
};
