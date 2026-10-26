# MDIB_ANALYSIS — Schemas, template and Dashboard requirements for a provider MDIB

Analysis only. No design, no MDIB written, no existing code changed. Files created: this report and `tools/validate_mdib.py`.
Note (2026-10-26): the reference provider analysed in Part B (`correct_provider.py`) and the material of the second, C++-based stack are no longer part of the repository; the template now lives at `testbed/mdib/template/correct_mdib.xml`.

## Inputs and their status

| Input | Status |
|---|---|
| Schemas `.venv/Lib/site-packages/sdc11073/xsd/` (11 files) | present, read |
| Template `testbed/mdib/template/correct_mdib.xml` (295 lines) | present, read |
| MDIB of the second (C++) stack | not available; the myMDIB comparison in A6 is therefore not possible |
| Dashboard source (`Dashboard/main.py`, `Dashboard/device/*.py`, `Dashboard/app/**/*.py`, `Dashboard/config/clinical_db.json`) | present, read |

Version fact: the venv contains **sdc11073 3.0.0** (`.venv/Lib/site-packages/sdc11073-*.dist-info/METADATA` line 3: `Version: 3.0.0`), not 2.3.0. All sdc11073 line references below are to 3.0.0, Python 3.13, lxml 6.1.3.

Notation: `PM:n` = `BICEPS_ParticipantModel.xsd` line n; `MSG:n` = `BICEPS_MessageModel.xsd` line n; `T:n` = `correct_mdib.xml` line n; other files by path.

---

# PART A — Schemas and template

## A1. Inventory

**BICEPS schemas (IEEE 11073-10207-2017)**

| File | targetNamespace | Defines | Imports |
|---|---|---|---|
| `BICEPS_ParticipantModel.xsd` (205 kB) | `http://standards.ieee.org/downloads/11073/11073-10207-2017/participant` (PM:2) | The participant model: `Mdib`, `MdDescription`, `MdState` (PM:10-51), all descriptor and state complex types, `CodedValue`, `InstanceIdentifier`, `LocalizedText`, `Range`, `Measurement`, context types, all enumerations. **No global element declarations** (grep for top-level `<xsd:element name=` returns nothing), only types. | `ExtensionPoint.xsd` (PM:3) |
| `BICEPS_MessageModel.xsd` (75 kB) | `…/11073-10207-2017/message` (MSG:2) | Global message elements of the Get/Set/Context/Event/Description services: `GetMdib`, `GetMdibResponse` (MSG:212, extends `AbstractGetResponse` MSG:134, child `Mdib` of type `pm:Mdib` MSG:222), `GetMdState`, `GetContextStates`, `SetAlertState`, `SetContextState`, `Episodic*Report`, `OperationInvokedReport`, `SystemErrorReport`, `DescriptionModificationReport`, `AbstractReport` (MSG:161) | `ExtensionPoint.xsd`, `BICEPS_ParticipantModel.xsd` (MSG:3-4) |
| `ExtensionPoint.xsd` (1.5 kB) | `…/11073-10207-2017/extension` (line 2) | One element `ext:Extension` of `ExtensionType` = `xsd:any namespace="##other" processContents="lax"`, plus attribute `MustUnderstand` (default false) | — |

**Transport schemas**

| File | targetNamespace | Defines |
|---|---|---|
| `soap-envelope.xsd` | `http://www.w3.org/2003/05/soap-envelope` (line 17) | SOAP 1.2 Envelope/Header/Body/Fault |
| `ws-addr.xsd` | `http://www.w3.org/2005/08/addressing` (line 18) | WS-Addressing 1.0: EndpointReference, ReferenceParameters, MessageID, Action, RelatesTo |
| `eventing.xsd` | `http://schemas.xmlsoap.org/ws/2004/08/eventing` (line 28) | WS-Eventing: Subscribe/SubscribeResponse, Renew, GetStatus, Unsubscribe, SubscriptionEnd, `Identifier`, `FilterType`; imports xml.xsd and the 2005/08 addressing namespace (lines 35-41) |
| `MetadataExchange.xsd` | `http://schemas.xmlsoap.org/ws/2004/09/mex` (line 44) | WS-MetadataExchange: GetMetadata, Metadata, MetadataSection |
| `wsdd-discovery-1.1-schema-os.xsd` | `http://docs.oasis-open.org/ws-dd/ns/discovery/2009/01` (line 22) | WS-Discovery 1.1: Hello, Bye, Probe, ProbeMatch, Resolve, ResolveMatch, Scopes, Types, AppSequence |
| `wsdd-dpws-1.1-schema-os.xsd` | `http://docs.oasis-open.org/ws-dd/ns/dpws/2009/01` (line 22) | DPWS 1.1: `ThisModel`/`ThisModelType` (33-34), `ThisDevice`/`ThisDeviceType` (52-53), `LocalizedStringType` (67), Relationship, Host, Hosted |
| `wsdl.xsd` | `http://schemas.xmlsoap.org/wsdl/` (line 34) | WSDL 1.1 (used for the hosted-service metadata) |
| `xml.xsd` | `http://www.w3.org/XML/1998/namespace` (line 3) | `xml:lang`, `xml:space`, `xml:base` attributes only |

There is **no MDPWS schema file** in the folder. sdc11073 maps the MDPWS namespace without a schema (`sdc11073/namespaces.py` has no MDPWS xsd entry among lines 42-113). Schemas import each other by absolute URL; `sdc11073/namespaces.py:33-113` maps each URL to the local file and `sdc11073/schema_resolver.py:49-84` resolves them offline.

## A2. Namespace check

| Namespace | Schema | Template |
|---|---|---|
| participant | `…/11073-10207-2017/participant` (PM:2) | `xmlns:pm="http://standards.ieee.org/downloads/11073/11073-10207-2017/participant"` (T:2) |
| message | `…/11073-10207-2017/message` (MSG:2) | `xmlns:msg="…/11073-10207-2017/message"` (T:2) |
| extension | `…/11073-10207-2017/extension` (ExtensionPoint.xsd:2) | `xmlns:ext="…/11073-10207-2017/extension"` (T:2) |

All three match character for character. Analysis continues.

## A3. BICEPS types

Conventions: child elements are listed in schema order; `[min..max]` occurrence; "req." = `use="required"`; every attribute not marked req. is optional. Every descriptor inherits from `AbstractDescriptor` (PM:619): children `ext:Extension [0..1]`, `Type : CodedValue [0..1]` (PM:624-625); attributes `Handle : pm:Handle` **req.** (PM:631), `DescriptorVersion : VersionCounter` (PM:636), `SafetyClassification` (PM:641). Every state inherits from `AbstractState` (PM:647): child `ext:Extension [0..1]`; attributes `StateVersion` (PM:654), `DescriptorHandle : HandleRef` **req.** (PM:659), `DescriptorVersion : ReferencedVersion` (PM:664). `Handle` and `HandleRef` are `xsd:string` with `minLength 1` (PM:583, 591). No attribute in the participant schema carries an XSD `default` (the dumper printed none); all defaults BICEPS speaks of are "implied values" in documentation text only.

### Descriptors

**AbstractDeviceComponentDescriptor** (PM:693) adds `ProductionSpecification [0..*]` (SpecType CodedValue [1], ProductionSpec string [1], ComponentId InstanceIdentifier [0..1]) (PM:700-716).
**AbstractComplexDeviceComponentDescriptor** (PM:728) adds `AlertSystem : AlertSystemDescriptor [0..1]` (PM:735), `Sco : ScoDescriptor [0..1]` (PM:740).

| Type | Children in order (after inherited) | Attributes |
|---|---|---|
| **MdsDescriptor** (PM:915) | `MetaData [0..1]` (PM:924; inside: Extension, `Udi [0..*]` {DeviceIdentifier [1], HumanReadableForm [1], Issuer InstanceIdentifier [1], Jurisdiction [0..1]}, LotNumber, `Manufacturer : LocalizedText [0..*]`, ManufactureDate, ExpirationDate, `ModelName : LocalizedText [0..*]`, ModelNumber, `SerialNumber [0..*]`), `SystemContext [0..1]` (PM:1002), `Clock [0..1]` (PM:1007), `Battery [0..*]`, `ApprovedJurisdictions [0..1]`, `Vmd : VmdDescriptor [0..*]` (PM:1022) | inherited only. Nothing is mandatory except `Handle`. |
| **VmdDescriptor** (PM:1084) | `ApprovedJurisdictions [0..1]`, `Channel : ChannelDescriptor [0..*]` (PM:1098) | inherited only |
| **ChannelDescriptor** (PM:1123) | `Metric : AbstractMetricDescriptor [0..*]` (PM:1132) — concrete type chosen by `xsi:type` | inherited only |
| **AbstractMetricDescriptor** (PM:2039) | `Unit : CodedValue [1..1]` **mandatory** (PM:2046), `BodySite [0..*]`, `Relation [0..*]` (PM:2058; attrs Kind req. enum Rcm/PS/SST/ECE/DCE/Oth, Entries req.) | `MetricCategory` **req.** (PM:2129), `DerivationMethod`, `MetricAvailability` **req.** (PM:2143), `MaxMeasurementTime`, `MaxDelayTime`, `DeterminationPeriod`, `LifeTimePeriod`, `ActivationDuration` (all xsd:duration) |
| **NumericMetricDescriptor** (PM:2227) | `TechnicalRange : Range [0..*]` (PM:2234) | `Resolution : xsd:decimal` **req.** (PM:2240), `AveragingPeriod : duration` (PM:2245) |
| **AlertSystemDescriptor** (PM:1216) | `AlertCondition : AlertConditionDescriptor [0..*]` (PM:1225), `AlertSignal : AlertSignalDescriptor [0..*]` (PM:1230) | `MaxPhysiologicalParallelAlarms : unsignedInt`, `MaxTechnicalParallelAlarms : unsignedInt`, `SelfCheckPeriod : xsd:duration` (PM:1236-1246) |
| **AlertConditionDescriptor** (PM:1389) | `Source : HandleRef [0..*]` (PM:1398), `CauseInfo [0..*]` (PM:1406; RemedyInfo [0..1], Description LocalizedText [0..*]) | `Kind : AlertConditionKind` **req.** (PM:1412), `Priority : AlertConditionPriority` **req.** (PM:1417), `DefaultConditionGenerationDelay : duration`, `CanEscalate` enum Lo/Me/Hi, `CanDeescalate` enum Me/Lo/None (PM:1424-1453) |
| **LimitAlertConditionDescriptor** (PM:1522, extends AlertConditionDescriptor) | `MaxLimits : Range [1..1]` **mandatory** (PM:1529) | `AutoLimitSupported : boolean` (PM:1539) |
| **AlertSignalDescriptor** (PM:1638) | none beyond inherited | `ConditionSignaled : HandleRef` (PM:1646), `Manifestation : AlertSignalManifestation` **req.** (PM:1653), `Latching : boolean` **req.** (PM:1658), `DefaultSignalGenerationDelay`, `MinSignalGenerationDelay`, `MaxSignalGenerationDelay` (durations), `SignalDelegationSupported : boolean`, `AcknowledgementSupported : boolean`, `AcknowledgeTimeout : duration` (PM:1665-1694) |
| **ScoDescriptor** (PM:2470) | `Operation : AbstractOperationDescriptor [0..*]` (PM:2477) — concrete type via `xsi:type` | inherited only |
| **AbstractOperationDescriptor** (PM:2532) | none | `OperationTarget : HandleRef` **req.** (PM:2538), `MaxTimeToFinish : duration`, `InvocationEffectiveTimeout : duration`, `Retriggerable : boolean`, `AccessLevel` enum Usr/CSUsr/RO/SP/Oth (PM:2543-2562) |
| **AbstractSetStateOperationDescriptor** (PM:2601) | `ModifiableData : xsd:string [0..*]` (PM:2608) | — |
| **SetContextStateOperationDescriptor** (PM:2761) | nothing further | nothing further |
| **SetAlertStateOperationDescriptor** (PM:2809) | nothing further | nothing further |
| **SystemContextDescriptor** (PM:3024) | `PatientContext [0..1]` (PM:3031), `LocationContext [0..1]` (PM:3038), `EnsembleContext [0..*]` (PM:3045), `OperatorContext [0..*]`, `WorkflowContext [0..*]` (PM:3059), `MeansContext [0..*]` | inherited only |
| **PatientContextDescriptor** (PM:3264), **LocationContextDescriptor** (PM:3448), **EnsembleContextDescriptor** (PM:3814), **WorkflowContextDescriptor** (PM:3475) | all extend `AbstractContextDescriptor` (PM:3086) which adds nothing to `AbstractDescriptor` | inherited only (`Handle` req.) |
| **ClockDescriptor** (PM:2834) | `TimeProtocol : CodedValue [0..*]` (PM:2841) | `Resolution : duration` (PM:2848) |

### States

**AbstractDeviceComponentState** (PM:866): children `CalibrationInfo [0..1]`, `NextCalibration [0..1]`, `PhysicalConnector [0..1]`; attrs `ActivationState : ComponentActivation`, `OperatingHours : unsignedInt`, `OperatingCycles : int` (PM:889-899).
**AbstractAlertState** (PM:1159): attr `ActivationState : AlertActivation` **req.** (PM:1165).
**AbstractMultiState** (PM:670): child `Category : CodedValue [0..1]`; attr `Handle : pm:Handle` **req.** (PM:684).
**AbstractContextState** (PM:3121): children `Validator : InstanceIdentifier [0..*]` (PM:3128), `Identification : InstanceIdentifier [0..*]` (PM:3133); attrs `ContextAssociation` (PM:3142), `BindingMdibVersion`, `UnbindingMdibVersion`, `BindingStartTime : Timestamp`, `BindingEndTime : Timestamp` (PM:3151-3166).
**AbstractOperationState** (PM:2643): attr `OperatingMode : OperatingMode` **req.** (PM:2649).

| Type | Children | Attributes |
|---|---|---|
| **MdsState** (PM:1058) | `OperatingJurisdiction [0..1]` (PM:1065) | `Lang : xsd:language`, `OperatingMode : MdsOperatingMode` (Nml/Dmo/Srv/Mtn, PM:1031) |
| **VmdState** (PM:1107) | `OperatingJurisdiction [0..1]` | inherited |
| **ChannelState** (PM:1141) | none | inherited |
| **AbstractMetricState** (PM:2187) | `BodySite [0..*]`, `PhysicalConnector [0..1]` | `ActivationState : ComponentActivation`, `ActiveDeterminationPeriod`, `LifeTimePeriod` (PM:2208-2218) |
| **NumericMetricState** (PM:2255) | `MetricValue : NumericMetricValue [0..1]` (PM:2262), `PhysiologicalRange : Range [0..*]` (PM:2267) | `ActiveAveragingPeriod : duration` (PM:2275) |
| **NumericMetricValue** (PM:1885) / **AbstractMetricValue** (PM:1818) | `MetricQuality [1..1]` **mandatory** (PM:1824; attr `Validity : MeasurementValidity` **req.** PM:1832, `Mode : GenerationMode` Real/Test/Demo PM:1839, `Qi`), `Annotation [0..*]` (Type CodedValue [1]) | `Value : xsd:decimal` (PM:1891), `StartTime`, `StopTime`, `DeterminationTime : Timestamp` (PM:1869-1879) |
| **AlertSystemState** (PM:1254) | `SystemSignalActivation [0..*]` (PM:1261; attrs Manifestation req., State : AlertActivation req., PM:1205-1210) | **self-check attributes:** `LastSelfCheck : Timestamp` (PM:1279, doc: "SHALL be updated on every pm:AlertSystemDescriptor/pm:SelfCheckPeriod"), `SelfCheckCount : xsd:long` (PM:1284); also `PresentPhysiologicalAlarmConditions`, `PresentTechnicalAlarmConditions : AlertConditionReference` (list of HandleRef, PM:1480) (PM:1291-1296). Plus inherited `ActivationState` req. |
| **AlertConditionState** (PM:1486) | none | `ActualConditionGenerationDelay`, `ActualPriority : AlertConditionPriority`, `Rank : int`, `Presence : xsd:boolean` (PM:1509), `DeterminationTime : Timestamp` (PM:1514); inherited `ActivationState` req. |
| **LimitAlertConditionState** (PM:1576) | `Limits : Range [1..1]` **mandatory** (PM:1583) | `MonitoredAlertLimits : AlertConditionMonitoredLimits` **req.** (PM:1591), `AutoLimitActivationState : AlertActivation` (PM:1598) |
| **AlertSignalState** (PM:1746) | none | `ActualSignalGenerationDelay`, `Presence : AlertSignalPresence` (PM:1757), `Location : AlertSignalPrimaryLocation`, `Slot : unsignedInt` (PM:1752-1767); inherited `ActivationState` req. |
| **ScoState** (PM:2486) | `OperationGroup [0..*]` (PM:2493; Type CodedValue [1], attrs OperatingMode, Operations) | `InvocationRequested : OperationRef`, `InvocationRequired : OperationRef` (PM:2519-2524) |
| **SetContextStateOperationState** (PM:2769), **SetAlertStateOperationState** (PM:2817), **SetMetricStateOperationState** (PM:2785), **SetComponentStateOperationState** (PM:2801), **ActivateOperationState** (PM:2753) | none | only inherited `OperatingMode` **req.** |
| **SetValueOperationState** (PM:2666) | `AllowedRange : Range [0..*]` (PM:2673) | `OperatingMode` req. |
| **SetStringOperationState** (PM:2698) | `AllowedValues [0..1]` → `Value : string [1..*]` (PM:2705-2711) | `OperatingMode` req. |
| **SystemContextState** (PM:3077) | none | inherited component attrs |
| **PatientContextState** (PM:3433) | `CoreData : PatientDemographicsCoreData [0..1]` (PM:3442) | inherited: `Handle` req., `ContextAssociation` … |
| **LocationContextState** (PM:3456) | `LocationDetail [0..1]` (PM:3463) | inherited |
| **EnsembleContextState** (PM:3822) | none | inherited |
| **WorkflowContextState** (PM:3674) | `WorkflowDetail [0..1]` (anonymous type, PM:3681): `ext:Extension [0..1]`, **`Patient : PersonReference [1..1]`** (PM:3688), `AssignedLocation : LocationReference [0..1]`, `VisitNumber : InstanceIdentifier [0..1]`, `DangerCode : CodedValue [0..*]` (PM:3703), `RelevantClinicalInfo : ClinicalInfo [0..*]` (PM:3708), `RequestedOrderDetail [0..1]` (PlacerOrderNumber [1] mandatory PM:3731), `PerformedOrderDetail [0..1]` | inherited |
| **ClockState** (PM:2856) | `ActiveSyncProtocol : CodedValue [0..1]`, `ReferenceSource : string [0..*]` | `DateAndTime : Timestamp`, **`RemoteSync : boolean` req.** (PM:2883), `Accuracy : decimal`, `LastSet : Timestamp`, `TimeZone`, `CriticalUse : boolean` |

### Data types

| Type | Definition |
|---|---|
| **CodedValue** (PM:296) | children: `ext:Extension [0..1]`, `CodingSystemName : LocalizedText [0..*]` (PM:314), `ConceptDescription : LocalizedText [0..*]` (PM:319), `Translation [0..*]` (PM:324; attrs Code req., CodingSystem, CodingSystemVersion). Attributes: `Code : CodeIdentifier` **req.** (string minLength 1, PM:174, 350), `CodingSystem : xsd:anyURI` optional (PM:357), `CodingSystemVersion : string` (PM:366), `SymbolicCodeName` (PM:373). |
| **CodingSystem default** | The schema declares **no XSD default** on `CodedValue/@CodingSystem` (PM:357: `<xsd:attribute name="CodingSystem" type="xsd:anyURI">`, no `default=`). The documentation on PM:359 states: *"If no CODING SYSTEM is defined, the implied value SHALL be `urn:oid:1.2.840.10004.1.1.1.0.0.1`, which refers to ISO/IEC 11073-10101."* Type `pm:CodedValue`, file `BICEPS_ParticipantModel.xsd`. The same implied-value rule is restated in the type note PM:298-310 ("expanded" CodingSystem). Consequence: a `<pm:Unit Code="%"/>` without `CodingSystem` semantically claims that `%` is an IEEE 11073-10101 code. |
| **InstanceIdentifier** (PM:382) | children: `ext:Extension [0..1]`, `Type : CodedValue [0..1]` (PM:392), `IdentifierName : LocalizedText [0..*]` (PM:399; doc: display only, "Applications ought not to perform any decision-making … based on … this property"). Attributes: `Root` anyURI minLength 1, optional (PM:407-421); `Extension` string minLength 1, optional (PM:423-438). Both optional in XSD; documentation rules R5008/R5009 govern their combination. |
| **LocationDetail** (PM:3225) | child `ext:Extension [0..1]`; attributes all optional strings: `PoC` (PM:3232), `Room` (PM:3237), `Bed` (PM:3242), `Facility` (PM:3247), `Building` (PM:3252), `Floor` (PM:3257). |
| **WorkflowDetail** | not a named type; anonymous complex type of `WorkflowContextState/WorkflowDetail` (PM:3681-3760), content listed above. `PersonReference` (PM:3207): `Identification : InstanceIdentifier [1..*]` **mandatory** (PM:3213), `Name : BaseDemographics [0..1]`. `ClinicalInfo` (PM:3483): Type, Code, Criticality (Lo/Hi), Description, `RelatedMeasurement [0..*]` {`Value : Measurement [1]`, ReferenceRange}. |
| **PatientDemographicsCoreData** (PM:3345) extends **BaseDemographics** (PM:3174) | BaseDemographics children: `Givenname : string [0..1]`, `Middlename [0..*]`, `Familyname [0..1]`, `Birthname [0..1]`, `Title [0..1]` (PM:3180-3200). CoreData adds `Sex` (Unspec/M/F/Unkn, PM:3272), `PatientType` (Unspec/Ad/Ado/Ped/Inf/Neo/Oth, PM:3303), `DateOfBirth [0..1]`, `Height : Measurement [0..1]`, `Weight : Measurement [0..1]`, `Race : CodedValue [0..1]` (PM:3358-3388). `Measurement` (PM:474): `MeasurementUnit : CodedValue [1]` **mandatory**, `MeasuredValue : decimal` **req.** |
| **Range** (PM:441) | attrs `Lower`, `Upper`, `StepWidth`, `RelativeAccuracy`, `AbsoluteAccuracy` : decimal, all optional |
| **Timestamp** (PM:110) | `xsd:unsignedLong`; R5001: **milliseconds** since 1970-01-01 UTC; R5002 (PM:116): if any timestamp is used, the provider SHALL provide a `pm:ClockDescriptor`. |
| **MdibVersionGroup** (PM:137) | `MdibVersion : VersionCounter` optional (implied 0), **`SequenceId : xsd:anyURI` req.** (PM:150), `InstanceId : unsignedLong` optional. Used by `GetMdibResponse` (MSG:134-145) and by `pm:Mdib`. |
| **Mdib** (PM:10) | `ext:Extension [0..1]`, `MdDescription [0..1]` (`Mds [0..*]`, attr `DescriptionVersion`), `MdState [0..1]` (`State : AbstractState [0..*]`, attr `StateVersion`) — concrete state types again via `xsi:type`. |

## A4. Enumerations (all `xsd:string` restrictions)

| Concept | Type (line) | Allowed values |
|---|---|---|
| alert priority | `AlertConditionPriority` (PM:1358) | `Lo`, `Me`, `Hi`, `None` |
| signal manifestation | `AlertSignalManifestation` (PM:1611) | `Aud`, `Vis`, `Tan`, `Oth` |
| alert activation | `AlertActivation` (PM:1174) | `On`, `Off`, `Psd` |
| signal presence | `AlertSignalPresence` (PM:1702) | `On`, `Off`, `Latch`, `Ack` |
| operating mode | `OperatingMode` (PM:2621) | `Dis`, `En`, `NA` |
| context association | `ContextAssociation` (PM:3094) | `No`, `Pre`, `Assoc`, `Dis` |
| metric category | `MetricCategory` (PM:1952) | `Unspec`, `Msrmt`, `Clc`, `Set`, `Preset`, `Rcmm` |
| metric availability | `MetricAvailability` (PM:2014) | `Intr`, `Cont` |
| measurement validity | `MeasurementValidity` (PM:58) | `Vld`, `Vldated`, `Ong`, `Qst`, `Calib`, `Inv`, `Oflw`, `Uflw`, `NA` |
| safety classification | `SafetyClassification` (PM:494) | `Inf`, `MedA`, `MedB`, `MedC` |
| (related) alert condition kind | `AlertConditionKind` (PM:1336) | `Phy`, `Tec`, `Oth` |
| (related) component activation | `ComponentActivation` (PM:523) | `On`, `NotRdy`, `StndBy`, `Off`, `Shtdn`, `Fail` |
| (related) monitored alert limits | `AlertConditionMonitoredLimits` (PM:1547) | `All`, `LoOff`, `HiOff`, `None` |

## A5. DPWS ThisModel and ThisDevice (`wsdd-dpws-1.1-schema-os.xsd`)

`ThisModelType` (line 34), sequence: **`Manufacturer : LocalizedStringType` [1..unbounded] mandatory** (36), `ManufacturerUrl : anyURI [0..1]`, **`ModelName : LocalizedStringType` [1..unbounded] mandatory** (40), `ModelNumber : string [0..1]`, `ModelUrl : anyURI [0..1]`, `PresentationUrl : anyURI [0..1]`, `xs:any ##other lax`. `ThisDeviceType` (line 53): **`FriendlyName : LocalizedStringType` [1..unbounded] mandatory** (55), `FirmwareVersion : string [0..1]`, `SerialNumber : string [0..1]`, `xs:any`. `LocalizedStringType` (67) = `xs:string` with any foreign attribute (so `xml:lang`). Element order is fixed (`xs:sequence`). This is consistent with the earlier observation that a ThisModel without ModelName is rejected: ModelName has no `minOccurs="0"`.

## A6. Template structure (`correct_mdib.xml`)

**Root and wrapper.** Root is `msg:GetMdibResponse` with attribute `SequenceId="1"` (T:2). Its single child is `msg:Mdib MdibVersion="1" SequenceId="1"` (T:3), which wraps `pm:MdDescription DescriptionVersion="1"` (T:4-183) and `pm:MdState` (T:185-293). sdc11073 accepts either a `GetMdibResponse` root or a bare `Mdib` root when loading a file (`sdc11073/pysoap/msgreader.py:180-186`), but only the `GetMdibResponse` form is schema-valid because the participant schema has no global element (A1) and the loader validates the root before unwrapping (`msgreader.py:196`).

**Descriptor tree (48 descriptors)**

```
Mds mds_1 (T:5)  — no Type, no MetaData
├─ AlertSystem mds_al_system (T:6)  — empty
├─ Sco sco_mds (T:8)
│   ├─ SetContextStateOperation opSetPatCtx            → PC.mds0                    (T:9)
│   ├─ SetContextStateOperation set_ensemble_context_op → ens_context_descriptor     (T:10)
│   ├─ SetContextStateOperation set_workflow_context_op → wrkflow_context_descriptor (T:11)
│   └─ ActivateOperation actop.mds0_sco_0               → mds_1                      (T:12)
├─ SystemContext sys_context_1 (T:14)
│   ├─ PatientContext  PC.mds0                 (T:15)
│   ├─ LocationContext loc_context_descriptor  (T:16)
│   ├─ EnsembleContext ens_context_descriptor  (T:17)
│   └─ WorkflowContext wrkflow_context_descriptor (T:18)
├─ Clock clock, TimeProtocol Code=532225 "NTPv4" (T:20-24)
├─ Vmd vmd_1 (T:27)
│   ├─ AlertSystem al_system_temperature (T:28)
│   │   ├─ AlertCondition al_condition_temperature Kind=Phy Priority=None, Type MDC_ALERT_TEMP_HIGH @1.2.3.4.5, Source=temperature (T:29-34)
│   │   └─ AlertSignal al_signal_temperature ConditionSignaled=al_condition_temperature Aud, Latching=true, SignalDelegationSupported=true (T:35-40)
│   ├─ Sco sco_1 (T:42)
│   │   ├─ SetAlertStateOperation temperature_alert_control → al_signal_temperature, ModifiableData Presence/ActivationState/ActualSignalGenerationDelay (T:43-47)
│   │   ├─ SetMetricStateOperation temperature_threshold_control → temperature (T:48)
│   │   ├─ SetValueOperation numeric.ch0.vmd1_sco_0 → temperature (T:49)
│   │   └─ SetStringOperation string.ch0.vmd1_sco_0 → system_statemachine (T:50)
│   └─ Channel sense-hat_channel_1 (T:52)
│       ├─ NumericMetric device_health  Msrmt/Cont Res=1, Type 68480-6 @LOINC-OID, Unit "%", TechnicalRange 0..100 (T:53-59)
│       ├─ NumericMetric temperature    Msrmt/Cont Res=1, Type 8310-5  @LOINC-OID, Unit "°"            (T:60-65)
│       ├─ NumericMetric device_id      Set/Cont   Res=1, Type DEV-ID-LOCAL @1.2.3.4.5, Unit "id"      (T:66-71)
│       └─ EnumStringMetric system_statemachine Msrmt/Cont, Unit 262656, AllowedValue Idle/Running/Aborted/Complete (T:72-78)
├─ Vmd vmd_2 (T:83)
│   ├─ AlertSystem al_system_humidity (T:84)
│   │   ├─ AlertCondition al_condition_humidity Kind=Phy Priority=None, no Type, Source=humidity (T:85-87)
│   │   └─ AlertSignal al_signal_humidity → al_condition_humidity, no Type (T:88-90)
│   ├─ Sco sco_2: SetAlertStateOperation humidity_alert_control → al_signal_humidity (T:93-97); SetMetricStateOperation humidity_threshold_control → humidity (T:98)
│   └─ Channel sense-hat_channel_2: NumericMetric humidity Msrmt/Cont Res=1, Type 12812-6 @LOINC-OID, Unit "%" (T:100-107)
└─ Vmd vmd_vitals (T:114)
    ├─ AlertSystem al_system_vitals (T:115)
    │   ├─ AlertCondition al_hr_hi   Kind=Phy Priority=Hi, Type MDC_ALERT_HR_HIGH  @1.2.3.4.5, Source=hr_metric   (T:116-122)
    │   ├─ AlertCondition al_spo2_lo Kind=Phy Priority=Me, Type MDC_ALERT_SPO2_LOW @1.2.3.4.5, Source=spo2_metric (T:123-129)
    │   ├─ AlertSignal al_signal_hr_hi   → al_hr_hi,   Aud, Latching=true (T:130-137)
    │   └─ AlertSignal al_signal_spo2_lo → al_spo2_lo, Aud, Latching=true (T:138-145)
    ├─ Sco sco_vitals: SetAlertStateOperation hr_alert_control → al_signal_hr_hi (T:150-153); spo2_alert_control → al_signal_spo2_lo (T:154-157), ModifiableData Presence/ActivationState
    └─ Channel vitals_channel (T:160)
        ├─ NumericMetric hr_metric   Msrmt/Cont Res=1, Type 8867-4  @LOINC-OID, Unit "bpm", TechnicalRange 20..300 (T:161-169)
        └─ NumericMetric spo2_metric Msrmt/Cont Res=1, Type 59408-5 @LOINC-OID, Unit "%",   TechnicalRange 50..100 (T:170-178)
```
(`@LOINC-OID` = `CodingSystem="urn:oid:2.16.840.1.113883.6.1"`.)

**States (37 in the file)**, each linked to its descriptor by `@DescriptorHandle`; context states additionally have their own `@Handle`:

| Group | States (T lines) |
|---|---|
| Context (multi-state) | `PatientContextState` DescriptorHandle=PC.mds0 Handle=Patient_Context Assoc; Identification Root=1.2.840.10004.20701.1.1 Extension=example; CoreData with empty Givenname/Familyname, Height/Weight 0 unit "123" (T:186-200). `LocationContextState` loc_context_descriptor/loc_context_state Assoc, LocationDetail Room=OR-1 Facility=ICU Bed=MockBed (T:201-203). `EnsembleContextState` ens_context_descriptor/ens_context_state **ContextAssociation=No**, Identification Root=b1111111-… Extension=initial (T:204-208). `WorkflowContextState` wrkflow_context_descriptor/wrkflow_context_state Assoc; WorkflowDetail/Patient/Identification Root=1.2.840.10004.20701.1.1 Extension=example; DangerCode 40275004 @SNOMED; RelevantClinicalInfo Criticality=Hi, empty Description, RelatedMeasurement Value 0 unit "123" (T:209-228). |
| Component / alert | VmdState vmd_1 On; AlertSystemState al_system_temperature On; AlertConditionState al_condition_temperature On Presence=false; AlertSignalState al_signal_temperature On Presence=Off (T:231-234). Same pattern for vmd_2 (T:237-240) and vmd_vitals with two condition/signal pairs (T:243-248). |
| Operation | 12 operation states, all `OperatingMode="En"` (T:251-260, 263-264), one per operation descriptor. |
| Metric | NumericMetricState device_id (Value 0), device_health (0), EnumStringMetricState system_statemachine (Idle), humidity (0, PhysiologicalRange 18-35), temperature (36, 30-45), hr_metric (80, 40-180), spo2_metric (98, 80-100); every MetricValue carries `MetricQuality Validity="Vld"` (T:267-292). |

**Descriptors with no state in the file (11):** `mds_1`, `mds_al_system`, `sco_mds`, `sys_context_1`, `clock`, `sco_1`, `sense-hat_channel_1`, `sco_2`, `sense-hat_channel_2`, `sco_vitals`, `vitals_channel`. The schema does not require a state per descriptor (`MdState/State [0..*]`). The sdc11073 provider creates the missing single-states on load: `ProviderMdib.from_string` calls `mk_state_containers_for_all_descriptors()` (`sdc11073/mdib/providermdib.py:294`), which sets `LastSelfCheck=now, SelfCheckCount=1` for a created AlertSystemState and `LastSet=now` for ClockState (`sdc11073/mdib/providermdibxtra.py:101-126`); the created `ClockState` gets `RemoteSync=True` from the container default (`sdc11073/mdib/statecontainers.py:465`), which satisfies the required attribute when the MDIB is serialised. **A C++ provider that loads the same XML has no such guarantee**; whether its stack synthesises the 11 missing states is not visible here.

**Reference mechanisms used:** `State/@DescriptorHandle → Descriptor/@Handle`; `Operation/@OperationTarget → target descriptor handle` (context descriptor for SetContextState, alert signal for SetAlertState, metric for SetMetricState/SetValue/SetString, MDS for Activate); `AlertSignal/@ConditionSignaled → AlertCondition/@Handle`; `AlertCondition/pm:Source → metric handle`.

**myMDIB.xml comparison:** not possible, file absent (see Inputs).

## A7. Validation tool and baseline

`tools/validate_mdib.py` (created): compiles one wrapper schema importing extension, participant and message model from the sdc11073 xsd folder (auto-detected from the installed package, override with `--xsd-dir`), resolves the absolute IEEE/W3C/OASIS schemaLocations to local files by basename (no network), validates each file with lxml and prints every violation as `file:line:column: [type] message`. Exit code 0/1/2. A first version used bare Windows paths as `schemaLocation`; libxml2 skipped those imports silently and every root failed with "No matching global declaration". The tool now uses `file:///` URIs and self-checks that `msg:GetMdib` is a known global element before validating.

Result on the template:

```
schema folder : C:\Users\zheka\Documents\VSCode\sdc11073\.venv\Lib\site-packages\sdc11073\xsd
entry point   : BICEPS_MessageModel.xsd (+ participant, extension)

MyTests\correct_provider\correct_mdib.xml (root <msg:GetMdibResponse>): VALID (0 violations)
EXIT=0
```

Cross-check with sdc11073's own compiled schema (`MessageReader._xml_schema`, built by `sdc11073/schema_resolver.py:19-46` and used for every received message): **VALID**. Negative control: the same schema with `Mds/@Handle` removed reports `line 5: The attribute 'Handle' is required but missing`, so the check is active.

Where this schema is enforced at runtime:
- Provider load: `MessageReader.read_xml_text` validates the root node (`msgreader.py:190-197`; `validate=True` default `msgreader.py:98`). An invalid file raises `ValidationError` (`msgreader.py:31-44`).
- Dashboard: `SdcConsumer.from_wsd_service(..., validate=True)` default (`sdc11073/consumer/consumerimpl.py:846`), called without `validate` in `Dashboard/device/handler.py:271-274, 293-296`; every received message is validated twice, envelope and body (`msgreader.py:136-149`). The Dashboard's patch in `Dashboard/device/patches.py:44-85` relaxes only `MessageFactory._validate_node` for **outgoing** messages; incoming validation is untouched.

## A8. Template defects (schema-valid, semantically questionable)

| # | Lines | Finding |
|---|---|---|
| 1 | T:2, T:3 | `SequenceId="1"` — schema type is `xsd:anyURI` (PM:150); "1" is lexically a relative URI, not a globally unique identifier as MdibVersionGroup intends. sdc11073's provider replaces it with its own sequence id at runtime; a C++ loader may not. |
| 2 | T:5 | `Mds mds_1` has no `Type` (device type code) and no `MetaData` (Manufacturer/ModelName). Identity of the device lives only in DPWS ThisModel/ThisDevice set in `correct_provider.py:251-260`. |
| 3 | T:6-7 | `AlertSystem mds_al_system` is empty (no conditions, no signals); no state for it in the file. |
| 4 | T:12 | `ActivateOperation actop.mds0_sco_0` targets the MDS itself; no semantic. |
| 5 | T:29, 85 | `Kind="Phy"` on temperature/humidity conditions of an environmental sensor (SenseHat); `Priority="None"`. |
| 6 | T:30, 37, 118, 125, 134, 142 | Alert `Type/@Code` values are symbolic names (`MDC_ALERT_HR_HIGH`, …) under the placeholder OID `urn:oid:1.2.3.4.5`; these are not IEEE 11073-10101 numeric codes and the OID is not a registered coding system. |
| 7 | T:54, 102 | LOINC codes `68480-6` and `12812-6` fail the LOINC Mod-10 check digit (computed 3 and 4 respectively; verified offline with the Regenstrief algorithm). They are not valid LOINC numbers. `8867-4`, `8310-5`, `59408-5` pass the check digit (heart rate, body temperature, SpO2 by pulse oximetry). |
| 8 | T:57, 64, 70, 105, 167, 176 | Units `%`, `°`, `id`, `bpm` without `CodingSystem` → implied 11073-10101 (PM:359), where these strings are not codes. `°` is a symbol, not a unit. `262656` (T:73) is the only numeric unit code. |
| 9 | T:66-71 | `device_id` modelled as a `NumericMetricDescriptor` with `MetricCategory="Set"` and a placeholder type `DEV-ID-LOCAL`. |
| 10 | T:35-36, 88-89, 133, 141 | `Latching="true"` on every signal while the provider drives `Presence` Off directly (`correct_provider.py:172-175`); `SignalDelegationSupported="true"` without any remote signal. No `AcknowledgementSupported` although the Dashboard acknowledges via these signals. |
| 11 | T:28, 84, 115 | No `SelfCheckPeriod` on any AlertSystemDescriptor, and no `LastSelfCheck`/`SelfCheckCount` on the AlertSystemStates (T:232, 238, 244) → the Dashboard's COMM_FAILURE check can never trigger (B3). |
| 12 | T:20-24 | `Clock` descriptor present, but no `ClockState` in the file (required attribute `RemoteSync` is only supplied by sdc11073's auto-creation). |
| 13 | T:187, 212 | Patient identifier `Root="1.2.840.10004.20701.1.1"` is a bare OID string, not a `urn:oid:` URI; `Extension="example"` is a placeholder. `IdentifierName` elements are empty (T:188, 206). |
| 14 | T:191-198 | Empty `Givenname`/`Familyname`; `Height`/`Weight` `MeasuredValue="0"` with unit code `123` (placeholder). Dashboard shows patient name "Unknown" (`qtDeviceHandler.py:258`). |
| 15 | T:204-207 | `EnsembleContextState` with `ContextAssociation="No"` but carrying an Identification (Root is a bare UUID `b1111111-…`, Extension `initial`). The Dashboard clears it (`context_ops.py:86-87`). |
| 16 | T:215-217 | DangerCode `40275004` (SNOMED) is not among the odds-ratio keys in `config/clinical_db.json:22-28` (`59621000`, `44054006`, `I50.9`, `R57.0`) → neutral OR = 1.0 at runtime (`device_profile_repo.py:249-251`). |
| 17 | T:218-226 | `RelevantClinicalInfo` with empty `Description` and a `RelatedMeasurement` of value 0 / unit `123`; this element is what required the `RelatedMeasurement.from_node` monkey-patch on both sides (`correct_provider.py:68-73`, `Dashboard/device/patches.py:22-41`). |
| 18 | T:53, 60, 66, 101, 163, 172 | `Resolution="1"` on all numeric metrics including SpO2 and temperature; no `StepWidth`, so the Dashboard's rounding (`qtDeviceHandler.py:550-555`) is inactive. |
| 19 | T:9 | `opSetPatCtx` targets `PC.mds0` and is enabled, but the Dashboard never writes the PatientContext (only Ensemble and Workflow, B5). |
| 20 | whole file | 11 descriptors without states (A6); relies on loader behaviour. |

---

# PART B — What the Dashboard needs from a provider MDIB

## B1. Patient identifier

**Read path** — `Dashboard/app/alarms/ensemble_topology_manager.py:352-464` (`_extract_patient_and_room`, executed under the device `data_lock`):
1. Room: first `LocationContextState`, `LocationDetail.Room` (lines 376-381).
2. Patient id, primary: iterate all `WorkflowContextState`s; for each `WorkflowDetail.Patient.Identification[*]`: take `Extension` if non-empty (402-405), else the first `IdentifierName` text (406-412); first hit wins (413-414).
3. Fallback, only `if not patient_id` (422): iterate `PatientContextState`s, same `Extension` → `IdentifierName` order (428-440); if still empty, `CoreData.Givenname + ' ' + Familyname` (443-453).

So: WorkflowContext first, PatientContext as fallback — confirmed. The ensemble key is `(patient_id, room)` (535); a device with either missing is skipped from ensemble formation (528-533).

**Does the written WorkflowContextState keep the patient identifier?** `apply_fhir_contexts` (`Dashboard/device/context_ops.py:144-276`) copies the existing first `WorkflowContextState` with `mk_copy()` (215-217), so `WorkflowDetail.Patient` and its `Identification` are retained; only `WorkflowDetail.DangerCode` is replaced (248) and `ContextAssociation` set to Assoc (223). If no WorkflowContextState exists, `mk_proposed_context_object(descriptor.Handle)` creates a fresh state (219-221) whose `WorkflowDetail` is `None`; the function then logs and returns without writing (243-247). Therefore the write never removes a patient identifier, and a provider without a pre-existing WorkflowContextState never receives DangerCodes.

**WorkflowContextState without a patient identifier:** the schema makes `WorkflowDetail/Patient` mandatory with at least one `Identification` (PM:3688, 3213), but `Extension` and `IdentifierName` are both optional. If none of them yields text, `patient_id` stays `None` after the loop and the PatientContext fallback at line 422 runs. Yes, the fallback happens.

Note the order of operations: extraction (526) precedes the FHIR write (594), so a DangerCode write affects the identifier only on a later reconnect. The UI's `patientId` (`Dashboard/app/qtDeviceHandler.py:266-279`) uses a different chain: PatientContextState `Identification[*].Extension` → patient name; it never reads WorkflowContext, so UI id and ensemble id can differ when the WorkflowContext carries a different identifier.

## B2. Alarm channel identity

- The alert callback iterates `alert_by_handle`, keyed by the state's `DescriptorHandle` (`handler.py:784`), and passes that handle as `alert_key` to `check_alert_validity(self.ensemble_uuid, handle, metric_concept, …)` (`handler.py:828-835`). `device_epr` is passed (833) but `DeviceAlertEvidence` has no device field (`Dashboard/app/alarms/alarmCoordinator.py`, fields `alert_key, metric_concept, manufacturer, model, ensemble_uuid, biceps_priority, reliability_profile`), and it is not used in `check_alert_validity` (`smartAlertAggregator.py:141-304`).
- The aggregator stores `_active_alarms[ensemble_uuid][alert_key]` (`smartAlertAggregator.py:75, 229`), i.e. **(ensemble, AlertCondition handle)**, not (device, handle).
- The full sensor registry is built by `_enumerate_member_specs` (`ensemble_topology_manager.py:173-228`): for every member device, every `AlertConditionDescriptor.Handle` becomes `alert_key` (`handler.py:963`) and is stored in one dict `specs[alert_key]` (topology 222) shared by all devices of the ensemble.
- UI cross-check `is_alarm_active(_ens_uuid, str(cond_handle))` is also keyed by handle (`qtDeviceHandler.py:370`, `smartAlertAggregator.py:540-552`).

**Two devices in one ensemble with the same AlertCondition handle:** the second device's `SensorSpec` overwrites the first in the registry (topology 222), so `|M|` counts one channel; both devices' alarms land in the same `_active_alarms` slot (one `DeviceAlertEvidence` overwrites the other, 229); the UI treats a stale signal of one device as active while the other's alarm is cached (`qtDeviceHandler.py:369-371`); `clear_alarm` from either device removes the shared slot (`smartAlertAggregator.py:597-600`). Handle uniqueness is only required per MDIB by BICEPS; the Dashboard implicitly requires uniqueness **per ensemble**.

## B3. Alert-system self-check (COMM_FAILURE)

`Dashboard/app/qtDeviceHandler.py:446-479` (inside `update_data`, executed on every UI tick — scheduled every 5 s from the monitoring loop, `handler.py:598, 629-630`, and rate-limited on metric/alert reports, `handler.py:750-752, 877-879`):
- For each `AlertSystemState` (462-465) it fetches the descriptor (467-469) and evaluates only `if als_desc.SelfCheckPeriod and als.LastSelfCheck` (470) — both must be present and non-zero; `SelfCheckCount` is never read anywhere in the Dashboard (grep).
- Skipped when `|clock_offset_sec| >= 3600` (472-473), where the offset comes from `ClockState.DateAndTime − time.time()` (415-442).
- Trigger: `time.time() − LastSelfCheck > SelfCheckPeriod × 1.5` (475-478). Units: sdc11073 converts the XML `Timestamp` (ms) to float seconds (`sdc11073/xml_types/dataconverters.py:120-131`) and the XML `xsd:duration` to float seconds (`dataconverters.py:259-264`, `xml_types/isoduration.py:38-56`).

Requirement for a provider: declare `SelfCheckPeriod` on the `AlertSystemDescriptor` and **update `LastSelfCheck` on the `AlertSystemState` at least every 1.5 × SelfCheckPeriod** (each update is an alert-state change, reported by `EpisodicAlertReport`); device clock within one hour of the Dashboard clock. `SelfCheckCount` is irrelevant to the Dashboard. If `SelfCheckPeriod` is absent, COMM_FAILURE is never raised. Constraint from sdc11073: the duration must match `PT[nH][nM][n[.f]S]` (`isoduration.py:32-36`); a value like `P1D` or `P0DT30S` raises `ValueError` (`isoduration.py:50-53`).

## B4. Alert condition descriptor types enumerated

- Alarm map and channel registry use `mdib.descriptions.NODETYPE.get(pm.AlertConditionDescriptor, [])` (`handler.py:426, 961`). sdc11073 indexes by exact `NODETYPE`; `LimitAlertConditionDescriptorContainer.NODETYPE = pm.LimitAlertConditionDescriptor` (`sdc11073/mdib/descriptorcontainers.py:706-709`), so **LimitAlertConditionDescriptors are not enumerated** into the registry or `|M|`.
- Incoming states are classified by substring `'Condition' in NODETYPE.localname` (`handler.py:884-891`), so a `LimitAlertConditionState` is processed as a Condition and reaches `check_alert_validity`; because it is absent from the registry it is not admitted while the registry is non-empty (`smartAlertAggregator.py:429-437`) and only produces the Yellow/ttl path (295-304).
- The UI highlights sources of both `AlertConditionState` and `LimitAlertConditionState` (`qtDeviceHandler.py:389-393`).
- Initial snapshot replays `AlertConditionState`, `AlertSignalState`, `AlertSystemState` exactly (`handler.py:546-553`); `LimitAlertConditionState` is **not** in that tuple, so a limit alarm already active at connect time is missed until its next report.

## B5. `apply_ensemble_context` and `apply_fhir_contexts`

`Dashboard/device/context_ops.py`:
- Descriptor: first `EnsembleContextDescriptor` (51-60) / first `WorkflowContextDescriptor` (180-189). Absent → warning, return (54-59, 183-188).
- **Operation handle:** linear search over all `SetContextStateOperationDescriptor`s for `op.OperationTarget == descriptor.Handle` (64-70, 199-205). No fallback; absent → warning, return (72-77, 207-213). The template satisfies this with `set_ensemble_context_op` (T:10) and `set_workflow_context_op` (T:11).
- **Create vs modify:** if a state of that type exists, `existing[0].mk_copy()` keeps its `Handle` → the provider sees an **update** of that context state (80-84, 215-217). If none exists, `context_service_client.mk_proposed_context_object(descriptor.Handle)` builds a new state whose `Handle` is set equal to the descriptor handle — sdc11073's convention for "insert a new context state" (`sdc11073/consumer/serviceclients/contextservice.py:48-57`). For the ensemble case the write proceeds with this new state; for the workflow case the new state has no `WorkflowDetail` and the write is abandoned (243-247). Whether a C++ provider honours the "Handle == descriptor handle means insert" convention is stack-internal and not visible here.
- Payload for the ensemble: all existing `Identification` entries cleared (86-87), one `InstanceIdentifier(root='bce837e3-0c46-4e52-af32-15bb36cfd746', extension=ensemble_uuid)` with `IdentifierName = ensemble_uuid` appended (98-106), `ContextAssociation = Assoc` (93). Sent with `set_context_state(operation_handle, [proposed])` (124-127); success sets `handler.ensemble_uuid` (128). Requires `consumer.context_service_client` (116-118, 258-260), i.e. the provider must host a Context service.

## B6. Everything the Dashboard reads or depends on

| MDIB / DPWS item | Where read | Effect / requirement |
|---|---|---|
| DPWS `ThisModel/Manufacturer[0]`, `ModelName[0]` | `handler.py:313-319` | Keys 1 and 2 of the profile lookup `device_profiles[manufacturer][model][concept]` (`handler.py:411-417`, `device_profile_repo.py:170-176`), exact string match. Missing → `''` → no profile → channel rejected from `|M|` (`ensemble_topology_manager.py:206-212`). |
| DPWS `ThisDevice/FriendlyName[0]` | `qtDeviceHandler.py:293` | Device name in UI. If absent, the fallback reads `mds.ModelName` (303), an attribute the sdc11073 `MdsDescriptorContainer` does not have (it exposes `MetaData` only, `descriptorcontainers.py:273-284`) → `AttributeError` → the whole `update_data` frame aborts (`qtDeviceHandler.py:655-656`) on every tick. FriendlyName is therefore effectively mandatory for the UI. |
| `NumericMetricDescriptor.Handle`, `Type/@Code` (fallback `Type/ConceptDescription[0]` text, fallback `Handle`) | `handler.py:376-389` | Semantic map handle → concept code; concept is key 3 of the profile lookup (415). Only **NumericMetricDescriptor** is mapped; a condition whose `Source` is a string/enum/waveform metric gets `metric_concept=''` → `check_alert_validity` returns `ESCALATE` fail-open (`smartAlertAggregator.py:174-175`). `CodingSystem` is ignored; codes are compared as plain strings with `clinical_db.json` keys. |
| `AlertConditionDescriptor.Handle` | `handler.py:963, 784` | Channel key (B2). |
| `AlertConditionDescriptor/pm:Source[*]` | `handler.py:919-923, 966-970`; `qtDeviceHandler.py:405-407` | First source handle that maps to a concept → `metric_concept`; UI highlights the source metrics. Must reference a **metric** handle (a device/MDS handle is not in the semantic map → `''`). |
| `AlertConditionDescriptor/@Priority` | `handler.py:925-929, 973-975` | Accepted values `Hi/Me/Lo/None` (matches PM:1358); anything else or absent → `'Hi'` fail-open; mapped to P_j via `clinical_db.json:14-20`. |
| `AlertConditionDescriptor/Type/@Code` | `handler.py:915-918`, `435` | Logging only. |
| `AlertSignalDescriptor/@ConditionSignaled` | `handler.py:931-933`; `qtDeviceHandler.py:359` | Links signal to condition for suppression/priority; a signal without it is never suppressed and never cross-checked. |
| `AlertSignalDescriptor/@Manifestation` | `handler.py:441` | Logging only. |
| `AlertConditionState/@Presence` (bool) | `handler.py:785, 870, 986-991`; `qtDeviceHandler.py:392` | Alarm ON/OFF edge; drives `check_alert_validity` / `clear_alarm` (818-822, 825-835). |
| `AlertSignalState/@Presence` (`On/Off/Ack/Latch`) | `qtDeviceHandler.py:346-380`; `handler.py:1012-1054` | UI status matrix On>Ack>Latch>Off; `Ack` starts a 30 s re-raise timer (`alarm_manager.py:30`, `handler.py:704-720`). |
| `AlertSystemState` | `handler.py:546-553, 789-790` | Replayed at connect but skipped in processing (no Presence). `@LastSelfCheck` + descriptor `@SelfCheckPeriod` → COMM_FAILURE (B3). |
| `SetAlertStateOperationDescriptor/@OperationTarget == AlertSignal handle` | `qtDeviceHandler.py:165-179`; `alarm_manager.py:153-177` | Ack sends `SetAlertState(op, AlertSignalState{Presence=Ack})` (`alarm_manager.py:85-86, 102-105`); the proposed state is a copy of the existing `AlertSignalState` (`sdc11073/mdib/consumermdibxtra.py:202-226`), so that state must exist. Fallback: first SetAlertState operation of any target (179, 173-174). The target must be the **signal**, not the condition. |
| `SetContextStateOperationDescriptor/@OperationTarget == EnsembleContextDescriptor.Handle` and `== WorkflowContextDescriptor.Handle` | `context_ops.py:64-70, 199-205` | Required for ensemble binding and FHIR write; no fallback. |
| `EnsembleContextDescriptor` (first), `EnsembleContextState` (first) | `context_ops.py:51-60, 80-87` | Binding target; existing state is updated in place (B5). |
| `WorkflowContextDescriptor` (first), `WorkflowContextState` (first, with `WorkflowDetail`) | `context_ops.py:180-189, 215-217, 243-248` | FHIR DangerCode write (B1). |
| `WorkflowContextState/WorkflowDetail/Patient/Identification[*]/@Extension` or `IdentifierName` | `ensemble_topology_manager.py:390-414` | Primary patient id. |
| `PatientContextState/Identification[*]/@Extension`, `IdentifierName`, `CoreData/Givenname+Familyname` | `ensemble_topology_manager.py:424-453`; `qtDeviceHandler.py:254-279` | Fallback patient id; UI name and id. |
| `LocationContextState[0]/LocationDetail/@Room` | `handler.py:457-460, 1070-1073`; `ensemble_topology_manager.py:376-381`; `qtDeviceHandler.py:240-247` | Room filter (`--room`, `main.py:49-59`, `handler.py:468-489`: mismatch → disconnect and ban for the session, `sdcMyConsumer.py:174-186`; **absent room → accepted** but excluded from ensembles, topology 528-533). `Bed`, `PoC`, `Facility` are never read. |
| Context states present in `GetMdibResponse`, else `GetContextStates` | `sdc11073/mdib/consumermdib.py:254-258`; `handler.py:358-372` | The provider must either include context states in GetMdib or serve `GetContextStates`. A Context service is also needed for the 5 s keep-alive ping (`handler.py:657-680`); without it the ping always "succeeds" (681) and only `is_connected`/metric silence can detect loss. |
| Operation states of all Set*/Activate types + descriptor `Type/ConceptDescription` or `Code`, `@OperatingMode` | `qtDeviceHandler.py:585-628` | UI operation list. |
| `NumericMetricState`, `StringMetricState`, `RealTimeSampleArrayMetricState`: `MetricValue/@Value`, `Samples`, `@DeterminationTime`; `NumericMetricDescriptor/TechnicalRange[0]/@StepWidth` | `qtDeviceHandler.py:492-575` | UI metric table; timestamps corrected by ClockState offset. |
| `ClockState/@RemoteSync`, `@DateAndTime` | `qtDeviceHandler.py:415-442` | Clock offset; warnings; gates the self-check. |
| `EpisodicMetricReport` cadence | `handler.py:603, 619-627` | Once a metric report has been received, silence > 120 s forces a reconnect. |
| `SequenceId` / `InstanceId` change | `handler.py:498-501, 1082-1094` | Forces reconnect (restart detection). |
| Subscriptions | `handler.py:277`: `start_all(not_subscribed_actions=periodic_actions)` (`sdc11073/xml_types/actions.py:63-68`) | The consumer subscribes to every non-periodic action the hosted service offers (`consumerimpl.py:600-630`), including `SystemErrorReport`, `OperationInvokedReport`, `EpisodicContextReport`, `DescriptionModificationReport`. |
| Reliability profile lookup | `config/clinical_db.json:30-63` | Existing keys: `Draeger/Infinity Monitor/8867-4`, `Draeger/Evita Ventilator/20053-5`, `BBraun/Space Perfusor/8775-2`, `Acme Medical/PatientMonitor Pro 3000/{8867-4, 59408-5, 8310-5, 12812-6}`, `Acme Medical/CardioMon 100/8867-4`, `Acme Medical/OxiPro 200/59408-5`. Admission requires `0 < false_alarm_rate < sensitivity` (`ensemble_topology_manager.py:215`). Check-digit note: `8775-2` (line 45) and `12812-6` (line 54) are not valid LOINC numbers (computed check digits 9 and 4); the lookup is a string match, so they still work as keys. |

---

# PART C — Implications

**Hard constraints from the schema (A1-A5, A7)**
1. Only a `msg:GetMdibResponse` root validates; `pm:Mdib` alone has no global element declaration. `SequenceId` is required on `GetMdibResponse` and typed `anyURI`.
2. Every descriptor needs `Handle`; metrics need `Unit`, `MetricCategory`, `MetricAvailability` (+ `Resolution` for numeric); alert conditions need `Kind` and `Priority`; alert signals need `Manifestation` and `Latching`; operations need `OperationTarget`; every state needs `DescriptorHandle`; context states need their own `Handle`; alert states need `ActivationState`; operation states need `OperatingMode`; `MetricValue` needs `MetricQuality/@Validity`; `ClockState` needs `RemoteSync`; `WorkflowDetail` needs `Patient/Identification`; `LimitAlertConditionDescriptor` needs `MaxLimits` and its state `Limits` + `MonitoredAlertLimits`.
3. Enumerated strings are exactly those in A4. Timestamps are integer milliseconds; any timestamp use obliges a `ClockDescriptor` (R5002). A `CodedValue` without `CodingSystem` is, by the standard's own note, an 11073-10101 code.
4. The schema does not require a state per descriptor; the template relies on sdc11073 synthesising 11 states. DPWS `Manufacturer`, `ModelName`, `FriendlyName` are mandatory elements.

**Hard requirements from the Dashboard (B1-B6)**
5. DPWS `Manufacturer`/`ModelName` strings must equal a `clinical_db.json` key pair exactly, and `FriendlyName` must be present (otherwise the UI update aborts).
6. Each alarm channel = one `AlertConditionDescriptor` (not Limit) whose first `Source` is a `NumericMetricDescriptor` with `Type/@Code` equal to the third `clinical_db.json` key; `Priority` ∈ {Hi, Me, Lo, None}; a matching `AlertSignalDescriptor` with `ConditionSignaled`; a `SetAlertStateOperationDescriptor` whose `OperationTarget` is that signal; the signal's state must exist.
7. `LocationContextState.Room` and a patient identifier (`WorkflowContext` Patient `Extension`, else `PatientContext` `Extension`, else names) must be identical strings on every device of one bed; otherwise no ensemble.
8. `EnsembleContextDescriptor` + `SetContextStateOperationDescriptor` targeting it + a Context service; for the FHIR path additionally a `WorkflowContextDescriptor`, its operation, and a pre-existing `WorkflowContextState` with `WorkflowDetail/Patient`.
9. AlertCondition handles must be unique across all devices of one ensemble (B2), not just within one MDIB.
10. COMM_FAILURE needs `SelfCheckPeriod` (PT-form) plus `LastSelfCheck` updates within 1.5 × period; metric reports at least every 120 s after the first; context states in GetMdib or via GetContextStates.

**Conflicts and open questions**
- (a) Schema says handles are unique per MDIB; the Dashboard's aggregation experiment (several devices per bed) needs uniqueness per ensemble. One shared MDIB file for several instances of one program collides here.
- (b) The room filter accepts a provider without a LocationContext (`handler.py:471-476`), but the topology manager excludes it from any ensemble and therefore from `SetContextState` and from the alarm filter (its alarms are shown raw). The "provider without LocationContext" scenario thus exercises acceptance but not aggregation.
- (c) The template is schema-valid but its unit and alert-type codes are not codes of the coding system the schema implies; the Dashboard never checks `CodingSystem`, so this is invisible at runtime. Two of its LOINC codes are not valid LOINC numbers.
- (d) The template has no `SelfCheckPeriod`, so the COMM_FAILURE feature is untested by the working example; enabling it constrains the provider to the `PTnS` duration form accepted by sdc11073 and to keeping `LastSelfCheck` fresh.
- (e) `LimitAlertConditionDescriptor` is schema-legal but never enters the Dashboard's sensor registry; only `AlertConditionDescriptor` channels are aggregated.
- (f) The Dashboard's insert-vs-update semantics for `SetContextState` rely on sdc11073's convention (new state's `Handle` equals the descriptor handle). How a non-sdc11073 provider interprets such a proposed state cannot be determined from these inputs.
- (g) The template omits 11 states and depends on the loader to create them (including `ClockState/@RemoteSync`, a required attribute). Whether a C++ stack does the same is not visible here; a `GetMdibResponse` that lacks a required attribute would be rejected by the Dashboard's incoming-message validation.
- (h) No MDIB of the second stack was available, so its format could not be compared.
- (i) The consumer library is sdc11073 3.0.0, not 2.3.0 as stated in the task; all behaviour above was read from 3.0.0.
