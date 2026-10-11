#include "SovereignMotionSidecarClient.h"

#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SovereignMotionJson.h"
#include "SovereignMotionSettings.h"

namespace
{
	/** The sidecar returns {"error": "..."} on failure; fall back to the raw body. */
	FString ExtractError(const FHttpResponsePtr& Response)
	{
		if (!Response.IsValid())
		{
			return TEXT("no response from sidecar (is `python -m sovereign_motion serve` running?)");
		}
		TSharedPtr<FJsonObject> Obj;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Response->GetContentAsString());
		FString Message;
		if (FJsonSerializer::Deserialize(Reader, Obj) && Obj.IsValid() && Obj->TryGetStringField(TEXT("error"), Message))
		{
			return FString::Printf(TEXT("HTTP %d: %s"), Response->GetResponseCode(), *Message);
		}
		return FString::Printf(TEXT("HTTP %d: %s"), Response->GetResponseCode(), *Response->GetContentAsString().Left(300));
	}
}

FSovereignMotionSidecarClient::FSovereignMotionSidecarClient()
{
	const USovereignMotionSettings* Settings = GetDefault<USovereignMotionSettings>();
	BaseUrl = Settings->SidecarUrl;
	TimeoutSeconds = Settings->RequestTimeoutSeconds;
}

FSovereignMotionSidecarClient::FSovereignMotionSidecarClient(const FString& InBaseUrl, float InTimeoutSeconds)
	: BaseUrl(InBaseUrl)
	, TimeoutSeconds(InTimeoutSeconds)
{
}

void FSovereignMotionSidecarClient::CheckHealth(FHealthCallback OnComplete) const
{
	const TSharedRef<IHttpRequest, ESPMode::ThreadSafe> HttpRequest = FHttpModule::Get().CreateRequest();
	HttpRequest->SetURL(BaseUrl / TEXT("health"));
	HttpRequest->SetVerb(TEXT("GET"));
	HttpRequest->SetTimeout(5.f);
	HttpRequest->OnProcessRequestComplete().BindLambda(
		[OnComplete](FHttpRequestPtr, FHttpResponsePtr Response, bool bConnected)
		{
			if (bConnected && Response.IsValid() && Response->GetResponseCode() == 200)
			{
				OnComplete(true, TEXT("Sidecar is running"));
			}
			else
			{
				OnComplete(false, ExtractError(Response));
			}
		});
	HttpRequest->ProcessRequest();
}

void FSovereignMotionSidecarClient::Generate(const FSovereignMotionRequest& Request, FGenerateCallback OnComplete) const
{
	const TSharedRef<IHttpRequest, ESPMode::ThreadSafe> HttpRequest = FHttpModule::Get().CreateRequest();
	HttpRequest->SetURL(BaseUrl / TEXT("generate"));
	HttpRequest->SetVerb(TEXT("POST"));
	HttpRequest->SetHeader(TEXT("Content-Type"), TEXT("application/json"));
	HttpRequest->SetTimeout(TimeoutSeconds);
	HttpRequest->SetContentAsString(SovereignMotionJson::RequestToJson(Request));
	HttpRequest->OnProcessRequestComplete().BindLambda(
		[OnComplete](FHttpRequestPtr, FHttpResponsePtr Response, bool bConnected)
		{
			FSovereignMotionResult Result;
			if (!bConnected || !Response.IsValid() || Response->GetResponseCode() != 200)
			{
				OnComplete(false, Result, FString(), ExtractError(Response));
				return;
			}
			const FString Body = Response->GetContentAsString();
			FString Error;
			if (!SovereignMotionJson::ResultFromJson(Body, Result, Error))
			{
				OnComplete(false, Result, Body, Error);
				return;
			}
			OnComplete(true, Result, Body, FString());
		});
	HttpRequest->ProcessRequest();
}
