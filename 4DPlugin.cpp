/* --------------------------------------------------------------------------------
 #
 #	4DPlugin.cpp
 #	source generated by 4D Plugin Wizard
 #	Project : Convert Color Space
 #	author : miyako
 #	2015/10/16
 #
 # --------------------------------------------------------------------------------*/


#include "4DPluginAPI.h"
#include "4DPlugin.h"

void PluginMain(PA_long32 selector, PA_PluginParameters params)
{
	try
	{
		PA_long32 pProcNum = selector;
		sLONG_PTR *pResult = (sLONG_PTR *)params->fResult;
		PackagePtr pParams = (PackagePtr)params->fParameters;

		CommandDispatcher(pProcNum, pResult, pParams); 
	}
	catch(...)
	{

	}
}

void CommandDispatcher (PA_long32 pProcNum, sLONG_PTR *pResult, PackagePtr pParams)
{
	switch(pProcNum)
	{
// --- Convert Color Space

		case 1 :
			Convert_color_space(pResult, pParams);
			break;

	}
}

// ------------------------------ Convert Color Space -----------------------------


void Convert_color_space(sLONG_PTR *pResult, PackagePtr pParams)
{
	C_LONGINT Param1;
	C_LONGINT Param2;
	C_LONGINT returnValue;

	Param1.fromParamAtIndex(pParams, 1);
	Param2.fromParamAtIndex(pParams, 2);

    NSUInteger color = Param1.getIntValue();
    CGFloat red, green, blue, alpha;
    
    alpha    = 1.0f;
    red      = (CGFloat)((color & 0x00FF0000) >> 16)  / 0xFF;
	green    = (CGFloat)((color & 0x0000FF00) >> 8)   / 0xFF;
	blue     = (CGFloat)((color & 0x000000FF))        / 0xFF;

    CGFloat components[4] = {red, green, blue, alpha};
    NSColor *tempColor;
    
	switch (Param2.getIntValue())
    {
        case 0:
            //from picker color
            tempColor = [NSColor colorWithColorSpace:[NSColorSpace genericRGBColorSpace]components:components count:4];
            tempColor = [tempColor colorUsingColorSpace:[NSColorSpace sRGBColorSpace]];
            [tempColor getRed:&red green:&green blue:&blue alpha:&alpha];
            color = 0x0000
                    +((NSUInteger)floor((red      * 0xFF) + 0.5f) << 16)
                    +((NSUInteger)floor((green    * 0xFF) + 0.5f) << 8)
                    + (NSUInteger)floor((blue     * 0xFF) + 0.5f);
        break;
            //to picker color
        default:
            tempColor = [NSColor colorWithColorSpace:[NSColorSpace sRGBColorSpace]components:components count:4];
            tempColor = [tempColor colorUsingColorSpace:[NSColorSpace genericRGBColorSpace]];
            
            [tempColor getRed:&red green:&green blue:&blue alpha:&alpha];
            color = 0x0000
                    +((NSUInteger)floor((red      * 0xFF) + 0.5f) << 16)
                    +((NSUInteger)floor((green    * 0xFF) + 0.5f) << 8)
                    + (NSUInteger)floor((blue     * 0xFF) + 0.5f);
        break;
}

    
    
    returnValue.setIntValue(color);
	returnValue.setReturn(pResult);
}

