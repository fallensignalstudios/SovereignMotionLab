#include "SovereignMotionJson.h"

#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace
{
	TArray<TSharedPtr<FJsonValue>> VecToJson(const FVector& V)
	{
		return {
			MakeShared<FJsonValueNumber>(V.X),
			MakeShared<FJsonValueNumber>(V.Y),
			MakeShared<FJsonValueNumber>(V.Z),
		};
	}

	TArray<TSharedPtr<FJsonValue>> QuatToJson(const FQuat& Q)
	{
		return {
			MakeShared<FJsonValueNumber>(Q.X),
			MakeShared<FJsonValueNumber>(Q.Y),
			MakeShared<FJsonValueNumber>(Q.Z),
			MakeShared<FJsonValueNumber>(Q.W),
		};
	}

	bool ReadVec(const TArray<TSharedPtr<FJsonValue>>& Values, FVector& Out)
	{
		if (Values.Num() != 3)
		{
			return false;
		}
		Out = FVector(Values[0]->AsNumber(), Values[1]->AsNumber(), Values[2]->AsNumber());
		return true;
	}

	bool ReadQuat(const TArray<TSharedPtr<FJsonValue>>& Values, FQuat& Out)
	{
		if (Values.Num() != 4)
		{
			return false;
		}
		Out = FQuat(Values[0]->AsNumber(), Values[1]->AsNumber(), Values[2]->AsNumber(), Values[3]->AsNumber());
		return true;
	}

	TSharedRef<FJsonObject> RootState(const FVector& Location, float YawDeg)
	{
		TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetArrayField(TEXT("root_location"), VecToJson(Location));
		Obj->SetNumberField(TEXT("root_yaw_deg"), YawDeg);
		return Obj;
	}
}

FString SovereignMotionJson::RequestToJson(const FSovereignMotionRequest& Request,
	FName TargetSkeletonName, const TArray<FSovereignMotionJoint>* TargetJoints)
{
	TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
	Root->SetStringField(TEXT("schema_version"), SchemaVersion);
	Root->SetStringField(TEXT("request_id"), Request.RequestId);
	Root->SetStringField(TEXT("backend"), Request.Backend);
	Root->SetNumberField(TEXT("seed"), Request.Seed);
	Root->SetNumberField(TEXT("fps"), Request.Fps);

	TSharedRef<FJsonObject> Character = MakeShared<FJsonObject>();
	Character->SetStringField(TEXT("skeleton_id"), Request.SkeletonId.ToString());
	if (!Request.Profile.IsEmpty())
	{
		Character->SetStringField(TEXT("profile"), Request.Profile);
	}
	if (TargetJoints && TargetJoints->Num() > 0)
	{
		TArray<TSharedPtr<FJsonValue>> Joints;
		for (const FSovereignMotionJoint& Joint : *TargetJoints)
		{
			TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
			Obj->SetStringField(TEXT("name"), Joint.Name.ToString());
			Obj->SetNumberField(TEXT("parent"), Joint.Parent);
			Obj->SetArrayField(TEXT("offset"), VecToJson(Joint.Offset));
			Joints.Add(MakeShared<FJsonValueObject>(Obj));
		}
		TSharedRef<FJsonObject> Skeleton = MakeShared<FJsonObject>();
		Skeleton->SetStringField(TEXT("name"), TargetSkeletonName.ToString());
		Skeleton->SetArrayField(TEXT("joints"), Joints);
		Character->SetObjectField(TEXT("skeleton"), Skeleton);
	}
	Root->SetObjectField(TEXT("character"), Character);

	Root->SetObjectField(TEXT("start"), RootState(Request.StartLocation, Request.StartYawDeg));
	TSharedRef<FJsonObject> Goal = RootState(Request.GoalLocation, Request.GoalYawDeg);
	if (Request.ArriveByS > 0.f)
	{
		Goal->SetNumberField(TEXT("arrive_by_s"), Request.ArriveByS);
	}
	Root->SetObjectField(TEXT("goal"), Goal);

	if (Request.Waypoints.Num() > 0)
	{
		TArray<TSharedPtr<FJsonValue>> Points;
		for (const FVector& P : Request.Waypoints)
		{
			Points.Add(MakeShared<FJsonValueArray>(VecToJson(P)));
		}
		TSharedRef<FJsonObject> Trajectory = MakeShared<FJsonObject>();
		Trajectory->SetArrayField(TEXT("waypoints"), Points);
		Root->SetObjectField(TEXT("trajectory"), Trajectory);
	}

	TSharedRef<FJsonObject> Locomotion = MakeShared<FJsonObject>();
	Locomotion->SetStringField(TEXT("style"), Request.LocomotionStyle);
	if (Request.SpeedCmS > 0.f)
	{
		Locomotion->SetNumberField(TEXT("speed_cm_s"), Request.SpeedCmS);
	}
	Root->SetObjectField(TEXT("locomotion"), Locomotion);

	if (Request.JointTargets.Num() > 0)
	{
		TArray<TSharedPtr<FJsonValue>> Targets;
		for (const FSovereignJointTarget& T : Request.JointTargets)
		{
			TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
			Obj->SetStringField(TEXT("joint"), T.Joint.ToString());
			Obj->SetNumberField(TEXT("time_s"), T.TimeS);
			if (T.bUseLocation)
			{
				Obj->SetArrayField(TEXT("location"), VecToJson(T.Location));
			}
			if (T.bUseRotation)
			{
				Obj->SetArrayField(TEXT("rotation"), QuatToJson(T.Rotation.Quaternion()));
			}
			Obj->SetBoolField(TEXT("hard"), T.bHard);
			Targets.Add(MakeShared<FJsonValueObject>(Obj));
		}
		Root->SetArrayField(TEXT("joint_targets"), Targets);
	}

	if (!Request.TextPrompt.IsEmpty())
	{
		Root->SetStringField(TEXT("text_prompt"), Request.TextPrompt);
	}

	if (Request.GameplayTags.Num() > 0)
	{
		TArray<TSharedPtr<FJsonValue>> Tags;
		for (const FString& Tag : Request.GameplayTags)
		{
			Tags.Add(MakeShared<FJsonValueString>(Tag));
		}
		TSharedRef<FJsonObject> Context = MakeShared<FJsonObject>();
		Context->SetArrayField(TEXT("tags"), Tags);
		Root->SetObjectField(TEXT("gameplay_context"), Context);
	}

	FString Out;
	const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
	FJsonSerializer::Serialize(Root, Writer);
	return Out;
}

bool SovereignMotionJson::ResultFromJson(const FString& Json, FSovereignMotionResult& OutResult, FString& OutError)
{
	TSharedPtr<FJsonObject> Root;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Json);
	if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
	{
		OutError = TEXT("result is not valid JSON");
		return false;
	}

	FString Version;
	if (!Root->TryGetStringField(TEXT("schema_version"), Version) || Version != SchemaVersion)
	{
		OutError = FString::Printf(TEXT("unsupported result schema_version '%s'"), *Version);
		return false;
	}

	FSovereignMotionResult Result;
	Root->TryGetStringField(TEXT("request_id"), Result.RequestId);
	Root->TryGetStringField(TEXT("backend"), Result.Backend);
	Root->TryGetNumberField(TEXT("fps"), Result.Fps);

	const TSharedPtr<FJsonObject>* Skeleton = nullptr;
	if (!Root->TryGetObjectField(TEXT("skeleton"), Skeleton))
	{
		OutError = TEXT("result has no skeleton");
		return false;
	}
	FString SkeletonName;
	(*Skeleton)->TryGetStringField(TEXT("name"), SkeletonName);
	Result.SkeletonName = FName(*SkeletonName);

	const TArray<TSharedPtr<FJsonValue>>* Joints = nullptr;
	if (!(*Skeleton)->TryGetArrayField(TEXT("joints"), Joints))
	{
		OutError = TEXT("result skeleton has no joints");
		return false;
	}
	for (const TSharedPtr<FJsonValue>& Value : *Joints)
	{
		const TSharedPtr<FJsonObject> Obj = Value->AsObject();
		if (!Obj.IsValid())
		{
			OutError = TEXT("skeleton joint is not an object");
			return false;
		}
		FSovereignMotionJoint Joint;
		Joint.Name = FName(*Obj->GetStringField(TEXT("name")));
		Joint.Parent = static_cast<int32>(Obj->GetNumberField(TEXT("parent")));
		// Parents must precede children; the baker and any FK rely on it.
		if (Joint.Parent >= Result.Joints.Num())
		{
			OutError = FString::Printf(TEXT("joint %s is listed before its parent"), *Joint.Name.ToString());
			return false;
		}
		if (!ReadVec(Obj->GetArrayField(TEXT("offset")), Joint.Offset))
		{
			OutError = FString::Printf(TEXT("joint %s has a malformed offset"), *Joint.Name.ToString());
			return false;
		}
		Result.Joints.Add(Joint);
	}

	const TArray<TSharedPtr<FJsonValue>>* Frames = nullptr;
	if (!Root->TryGetArrayField(TEXT("frames"), Frames))
	{
		OutError = TEXT("result has no frames");
		return false;
	}
	Result.Frames.Reserve(Frames->Num());
	for (int32 FrameIndex = 0; FrameIndex < Frames->Num(); ++FrameIndex)
	{
		const TSharedPtr<FJsonObject> Obj = (*Frames)[FrameIndex]->AsObject();
		if (!Obj.IsValid())
		{
			OutError = FString::Printf(TEXT("frame %d is not an object"), FrameIndex);
			return false;
		}
		FSovereignMotionFrame Frame;
		const TArray<TSharedPtr<FJsonValue>>& Rotations = Obj->GetArrayField(TEXT("rotations"));
		if (!ReadVec(Obj->GetArrayField(TEXT("root_translation")), Frame.RootTranslation)
			|| Rotations.Num() != Result.Joints.Num())
		{
			OutError = FString::Printf(TEXT("frame %d is malformed"), FrameIndex);
			return false;
		}
		Frame.Rotations.Reserve(Rotations.Num());
		for (const TSharedPtr<FJsonValue>& Rotation : Rotations)
		{
			FQuat Q;
			if (!ReadQuat(Rotation->AsArray(), Q))
			{
				OutError = FString::Printf(TEXT("frame %d has a malformed rotation"), FrameIndex);
				return false;
			}
			Frame.Rotations.Add(Q.GetNormalized());
		}
		Result.Frames.Add(MoveTemp(Frame));
	}

	const TSharedPtr<FJsonObject>* Contacts = nullptr;
	if (Root->TryGetObjectField(TEXT("foot_contacts"), Contacts))
	{
		for (const TSharedPtr<FJsonValue>& V : (*Contacts)->GetArrayField(TEXT("left")))
		{
			Result.LeftFootContacts.Add(V->AsBool());
		}
		for (const TSharedPtr<FJsonValue>& V : (*Contacts)->GetArrayField(TEXT("right")))
		{
			Result.RightFootContacts.Add(V->AsBool());
		}
	}

	const TSharedPtr<FJsonObject>* Diagnostics = nullptr;
	if (Root->TryGetObjectField(TEXT("diagnostics"), Diagnostics))
	{
		(*Diagnostics)->TryGetStringArrayField(TEXT("notes"), Result.Notes);
	}

	OutResult = MoveTemp(Result);
	return true;
}
