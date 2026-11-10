#include "4DPluginAPI.h"
#include "PrivateTypes.h"
#include "EntryPoints.h"

#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <unordered_map>

namespace {
std::unordered_map<PA_Unichar*, size_t> strings;
std::unordered_map<PA_Handle, size_t> handles;
struct Picture { unsigned refs; std::vector<uint8_t> bytes; };
std::unordered_map<PA_Picture, Picture*> pictures;
bool allocatedEmpty = true;
bool replacePicture = false;
bool rejectCommand = false;
CUTF16String expectedText;

void require(bool ok, const char* message)
{
    if (!ok) {
        std::fprintf(stderr, "ownership assertion: %s\n", message);
        std::abort();
    }
}

PA_Unistring makeString(const PA_Unichar* text)
{
    size_t n = 0;
    if (text) while (text[n]) ++n;
    PA_Unistring result = {};
    result.fLength = static_cast<PA_long32>(n);
    if (n || allocatedEmpty) {
        result.fString = new PA_Unichar[n + 1];
        if (n) std::memcpy(result.fString, text, n * sizeof(PA_Unichar));
        result.fString[n] = 0;
        PA_Unichar* buffer = result.fString;
        strings.emplace(buffer, n);
    }
    return result;
}

void checkString(PA_Unistring value, const CUTF16String& expected)
{
    require(value.fLength == expected.size(), "exact UTF-16 length");
    if (value.fString) {
        PA_Unichar* buffer = value.fString;
        auto it = strings.find(buffer);
        require(it != strings.end(), "live string allocation");
        require(it->second == expected.size(), "string allocation length");
        require(value.fString[value.fLength] == 0, "string terminator");
        require(CUTF16String(value.fString, value.fLength) == expected, "exact text");
    } else {
        require(expected.empty(), "null only represents empty text");
    }
}

void disposeString(PA_Unistring value)
{
    if (!value.fString) return;
    PA_Unichar* buffer = value.fString;
    require(strings.erase(buffer) == 1, "string double disposal");
    delete[] value.fString;
}

PA_Picture makePicture(const void* data, size_t size)
{
    Picture* p = new Picture{1, {}};
    if (size) {
        const uint8_t* bytes = static_cast<const uint8_t*>(data);
        p->bytes.assign(bytes, bytes + size);
    }
    pictures.emplace(p, p);
    return p;
}

Picture* livePicture(PA_Picture p)
{
    auto it = pictures.find(p);
    require(it != pictures.end(), "live picture owner");
    return it->second;
}

void disposePicture(PA_Picture p)
{
    if (!p) return;
    Picture* value = livePicture(p);
    if (--value->refs == 0) {
        pictures.erase(p);
        delete value;
    }
}

void clearVariable(PA_Variable* variable)
{
    switch (variable->fType) {
        case eVK_Unistring: disposeString(variable->uValue.fString); break;
        case eVK_Picture: disposePicture(variable->uValue.fPicture); break;
        case eVK_ArrayUnicode: {
            PA_Handle h = variable->uValue.fArray.fData;
            require(handles.count(h), "live array handle");
            auto* elements = reinterpret_cast<PA_Unistring*>(*h);
            for (PA_long32 i = 0; i <= variable->uValue.fArray.fNbElements; ++i)
                disposeString(elements[i]);
            PA_DisposeHandle(h);
            break;
        }
        case eVK_Undefined:
        case eVK_Longint: break;
        default: require(false, "unexpected variable cleanup");
    }
    *variable = {};
    variable->fType = eVK_Undefined;
}

void host(short selector, EngineBlock* eb)
{
    switch (selector) {
        case EX_CREATE_UNISTRING:
            eb->fUniString1 = makeString(reinterpret_cast<PA_Unichar*>(eb->fHandle));
            break;
        case EX_DISPOSE_UNISTRING: disposeString(eb->fUniString1); break;
        case EX_SET_UNISTRING: {
            PA_Unistring copy = makeString(reinterpret_cast<PA_Unichar*>(eb->fHandle));
            disposeString(eb->fUniString1);
            eb->fUniString1 = copy;
            break;
        }
        case EX_CREATE_PICTURE:
            eb->fPicture = makePicture(eb->fPtr1, eb->fParam1);
            eb->fError = 0;
            break;
        case EX_DISPOSE_PICTURE: disposePicture(eb->fPicture); break;
        case EX_DUPLICATE_PICTURE:
            if (eb->fPicture) {
                Picture* p = livePicture(eb->fPicture);
                if (eb->fParam1 == 0) ++p->refs;
                else eb->fPicture = makePicture(p->bytes.data(), p->bytes.size());
            }
            break;
        case EX_GET_PICTURE_DATA:
            livePicture(eb->fPicture);
            // No representations: output-string ownership is deliberately not modeled.
            eb->fUniString1 = {};
            eb->fError = eER_IncompatibleTypes;
            break;
        case EX_CLEAR_VARIABLE:
            clearVariable(static_cast<PA_Variable*>(eb->fPtr1));
            break;
        case EX_HANDLE_MANAGER: {
            PA_Handle h = eb->fParam1 == 1 ? nullptr : eb->fHandle;
            size_t size = eb->fParam1 == 1 ? eb->fParam2 : 0;
            switch (eb->fParam1) {
                case 1:
                    h = new char*;
                    *h = static_cast<char*>(std::calloc(eb->fParam2 ? eb->fParam2 : 1, 1));
                    handles.emplace(h, size);
                    eb->fHandle = h;
                    break;
                case 2:
                    require(handles.erase(h) == 1, "handle double disposal");
                    std::free(*h);
                    delete h;
                    break;
                case 3:
                    require(handles.count(h), "resize live handle");
                    *h = static_cast<char*>(std::realloc(*h, eb->fParam2 ? eb->fParam2 : 1));
                    require(*h, "test handle allocation");
                    handles[h] = eb->fParam2;
                    break;
                case 4:
                    require(handles.count(h), "size live handle");
                    eb->fParam2 = handles.at(h);
                    break;
                case 5:
                    require(handles.count(h), "lock live handle");
                    eb->fParam3 = reinterpret_cast<sLONG_PTR>(*h);
                    break;
                case 6: require(handles.count(h), "unlock live handle"); break;
                default: require(false, "unexpected handle operation");
            }
            eb->fError = 0;
            break;
        }
        case EX_CONVERT_CHARSET_TO_CHARSET: {
            require(eb->fParam2 == eVTC_UTF_8 && eb->fParam4 == eVTC_UTF_16,
                    "conversion direction");
            CFStringRef s = CFStringCreateWithBytes(kCFAllocatorDefault,
                static_cast<const UInt8*>(eb->fPtr1), eb->fParam1, kCFStringEncodingUTF8, false);
            require(s, "valid conversion input");
            CFIndex units = CFStringGetLength(s);
            size_t bytes = units * sizeof(PA_Unichar);
            require(bytes + sizeof(PA_Unichar) <= eb->fParam3, "conversion byte capacity");
            CFStringGetCharacters(s, CFRangeMake(0, units), static_cast<UniChar*>(eb->fPtr2));
            static_cast<PA_Unichar*>(eb->fPtr2)[units] = 0;
            // The SDK explicitly returns BYTES, excluding the terminator.
            eb->fParam3 = bytes;
            eb->fError = 0;
            CFRelease(s);
            break;
        }
        case EX_EXECUTE_COMMAND_BY_ID: {
            auto** args = static_cast<PA_Variable**>(eb->fPtr1);
            if (rejectCommand) {
                eb->fError = eER_IncompatibleTypes;
                break;
            }
            switch (eb->fParam1) {
                case 678:
                    require(args[0]->fType == eVK_Unistring, "file path argument type");
                    checkString(args[0]->uValue.fString, expectedText);
                    clearVariable(args[1]);
                    PA_SetPictureVariable(args[1], makePicture("file", 4));
                    break;
                case 1002:
                    require(args[1]->fType == eVK_Unistring, "format argument type");
                    checkString(args[1]->uValue.fString, expectedText);
                    livePicture(PA_GetPictureVariable(*args[0]));
                    if (replacePicture) {
                        clearVariable(args[0]);
                        PA_SetPictureVariable(args[0], makePicture("converted", 9));
                    }
                    break;
                case 988:
                    livePicture(PA_GetPictureVariable(*args[0]));
                    require(PA_GetLongintVariable(*args[1]) == 101, "grayscale operation");
                    if (replacePicture) {
                        clearVariable(args[0]);
                        PA_SetPictureVariable(args[0], makePicture("gray", 4));
                    }
                    break;
                case 457:
                    livePicture(PA_GetPictureVariable(*args[0]));
                    PA_SetLongintVariable(args[1], 17);
                    PA_SetLongintVariable(args[2], 23);
                    break;
                case 679:
                    livePicture(PA_GetPictureVariable(*args[0]));
                    require(PA_GetLongintVariable(*args[2]) == 5, "thumbnail width");
                    require(PA_GetLongintVariable(*args[3]) == 7, "thumbnail height");
                    clearVariable(args[1]);
                    PA_SetPictureVariable(args[1], makePicture("thumb", 5));
                    break;
                default: require(false, "unexpected command");
            }
            // These five commands are procedures, not owning function results.
            static_cast<PA_Variable*>(eb->fPtr2)->fType = eVK_Undefined;
            eb->fError = 0;
            break;
        }
        default: require(false, "unexpected engine selector");
    }
}

void balanced()
{
    require(strings.empty(), "string allocation balance");
    require(handles.empty(), "handle allocation balance");
    require(pictures.empty(), "picture reference balance");
}

void arrays()
{
    const PA_Unichar text[] = {'A', 0xe9, 0x65e5, 0xd83d, 0xde00, 0};
    const CUTF16String expected(text);
    PA_Variable a = PA_CreateVariable(eVK_ArrayUnicode);
    PA_ResizeArray(&a, 2);
    for (int i = 0; i <= 2; ++i) {
        PA_Unistring owned = PA_CreateUnistring(const_cast<PA_Unichar*>(text));
        PA_SetStringInArray(a, i, &owned);
    }
    PA_Unistring borrowed = PA_GetStringInArray(a, 1);
    C_TEXT copy;
    copy.setUTF16String(&borrowed);
    PA_Unistring independent = PA_CreateUnistring(borrowed.fString);
    require(independent.fString != borrowed.fString, "independent transferred owners");
    PA_SetStringInArray(a, 2, &independent);
    for (int i = 0; i != 32; ++i) {
        PA_Unistring churn = PA_CreateUnistring(const_cast<PA_Unichar*>(text));
        PA_DisposeUnistring(&churn);
    }
    PA_Unichar empty[] = {0};
    PA_Unistring replacement = PA_CreateUnistring(empty);
    PA_SetStringInArray(a, 1, &replacement);
    checkString(PA_GetStringInArray(a, 1), CUTF16String());
    require(CUTF16String(copy.getUTF16StringPtr(), copy.getUTF16Length()) == expected,
            "borrowed copy survives replacement");
    checkString(PA_GetStringInArray(a, 2), expected);
    PA_ResizeArray(&a, 0);
    checkString(PA_GetStringInArray(a, 0), expected);
    PA_ClearVariable(&a);

    for (int rejection = 0; rejection != 4; ++rejection) {
        PA_Variable destination = PA_CreateVariable(eVK_ArrayUnicode);
        PA_Unistring owned = PA_CreateUnistring(const_cast<PA_Unichar*>(text));
        PA_Variable invalid = destination;
        PA_long32 index = 0;
        if (rejection == 0) invalid.fType = eVK_Longint;
        if (rejection == 1) index = -1;
        if (rejection == 2) index = 1;
        if (rejection == 3) invalid.uValue.fArray.fData = nullptr;
        PA_SetStringInArray(invalid, index, &owned);
        checkString(owned, expected);
        PA_DisposeUnistring(&owned);
        PA_SetStringInArray(destination, 0, nullptr);
        checkString(PA_GetStringInArray(destination, 0), CUTF16String());
        PA_ClearVariable(&destination);
    }

    ARRAY_TEXT wrapper;
    wrapper.appendUTF16String(empty);
    wrapper.appendUTF16String(text);
    wrapper.appendUTF16String(text);
    PA_Variable output = PA_CreateVariable(eVK_Undefined);
    BytePtr parameters[] = {reinterpret_cast<BytePtr>(&output)};
    wrapper.toParamAtIndex(parameters, 1);
    checkString(PA_GetStringInArray(output, 1), expected);
    PA_Unistring first = PA_GetStringInArray(output, 1);
    PA_Unistring second = PA_GetStringInArray(output, 2);
    require(first.fString != second.fString, "wrapper creates independent owners");
    ARRAY_TEXT copied;
    copied.fromParamAtIndex(parameters, 1);
    wrapper.setSize(1);
    wrapper.toParamAtIndex(parameters, 1);
    PA_ClearVariable(&output);
    CUTF16String survived;
    copied.copyUTF16StringAtIndex(&survived, 2);
    require(survived == expected, "array wrapper copies borrowed text");
    balanced();
}

void scalar()
{
    const PA_Unichar text[] = {'A', 0xe9, 0xd83d, 0xde00, 0};
    PA_Unistring input = PA_CreateUnistring(const_cast<PA_Unichar*>(text));
    C_TEXT copy;
    BytePtr parameters[] = {reinterpret_cast<BytePtr>(&input)};
    copy.fromParamAtIndex(parameters, 1);
    PA_DisposeUnistring(&input);
    PA_Unistring output = {};
    parameters[0] = reinterpret_cast<BytePtr>(&output);
    copy.toParamAtIndex(parameters, 1);
    checkString(output, CUTF16String(text));
    PA_Unistring returned = {};
    PA_Unistring* result = &returned;
    copy.setReturn(reinterpret_cast<sLONG_PTR*>(&result));
    checkString(returned, CUTF16String(text));
    require(output.fString != returned.fString, "content setter copies independently");
    PA_DisposeUnistring(&output);
    PA_DisposeUnistring(&returned);
    PA_Unistring apiReturn = {};
    PA_Unistring* apiResult = &apiReturn;
    PluginBlock block = {nullptr, &apiResult, nullptr};
    PA_ReturnString(&block, const_cast<PA_Unichar*>(text));
    checkString(apiReturn, CUTF16String(text));
    PA_DisposeUnistring(&apiReturn);
    balanced();
}

struct TextFixture {
    const char* utf8;
    CUTF16String utf16;
};

std::vector<TextFixture> fixtures()
{
    const PA_Unichar ascii[] = {'A', 'S', 'C', 'I', 'I', 0};
    const PA_Unichar bmp[] = {0xe9, 0x65e5, 0};
    const PA_Unichar supplementary[] = {0xd83d, 0xde00, 0};
    return {{"ASCII", CUTF16String(ascii)},
            {"\xc3\xa9\xe6\x97\xa5", CUTF16String(bmp)},
            {"\xf0\x9f\x98\x80", CUTF16String(supplementary)},
            {"", CUTF16String()}};
}

void picturesTest(const std::string& mode)
{
    PA_Picture handoff = nullptr;
    {
        CPicture p;
        if (mode == "bytes") {
            const uint8_t data[] = {0x12, 0x34, 0x56};
            for (int i = 0; i != 3; ++i) p.setBytes(data, sizeof(data));
            require(livePicture(p.getPicture())->bytes == std::vector<uint8_t>(data, data + 3),
                    "setBytes preserves synthetic data");
        } else if (mode == "path") {
            for (const TextFixture& fixture : fixtures()) {
                CUTF8String u8(reinterpret_cast<const uint8_t*>(fixture.utf8));
                expectedText = fixture.utf16;
                p.fromFileAtPath(&u8);
                require(livePicture(p.getPicture())->refs == 1, "file argument released after retain");
            }
        } else if (mode == "copy") {
            for (const TextFixture& fixture : fixtures()) {
                expectedText = fixture.utf16;
                PA_Picture copy = p.createCopy(fixture.utf8);
                livePicture(copy);
                PA_DisposePicture(copy);
            }
            const PA_Unichar png[] = {'.', 'p', 'n', 'g', 0};
            expectedText = CUTF16String(png);
            handoff = p.createCopy(".png");
        } else if (mode == "gray") {
            handoff = p.createGrayScale();
        } else if (mode == "size") {
            unsigned width = 0, height = 0;
            p.getSize(&width, &height);
            require(width == (rejectCommand ? 0 : 17) && height == (rejectCommand ? 0 : 23),
                    "picture dimensions");
        } else if (mode == "thumbnail") {
            handoff = p.createThumbnail(5, 7);
        } else {
            require(false, "known test mode");
        }
    }
    if (handoff) {
        require(livePicture(handoff)->refs == 1, "owning handoff survives wrapper destruction");
        PA_DisposePicture(handoff);
    }
    balanced();
}

void dispatcher()
{
    PA_long32 color = 0, direction = 0, result = -1;
    BytePtr parameters[] = {reinterpret_cast<BytePtr>(&color), reinterpret_cast<BytePtr>(&direction)};
    PluginBlock block = {parameters, &result, nullptr};
    PluginMain(1, &block);
    require(result == 0, "actual scalar dispatcher remains functional");
    balanced();
}
}

int main(int argc, char** argv)
{
    require(argc == 3, "mode and empty representation required");
    allocatedEmpty = std::string(argv[2]) == "allocated";
    gCall4D = host;
    @autoreleasepool {
        std::string mode(argv[1]);
        if (mode == "arrays") arrays();
        else if (mode == "scalar") scalar();
        else if (mode == "dispatcher") dispatcher();
        else {
            for (int scenario = 0; scenario != 3; ++scenario) {
                replacePicture = scenario == 1;
                rejectCommand = scenario == 2;
                picturesTest(mode);
            }
        }
    }
    std::printf("PASS %s (%s empty)\n", argv[1], argv[2]);
}
